"""Run Chromium UI tests against disposable app infrastructure.

Uses a generated PostgreSQL database, upload directory, Redis prefix/queue, and
ephemeral loopback port. Does not read or mutate the live application's jobs.
"""
import os
import argparse
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from database import get_database_url
from models import Job, CsvAnalysis, OutboxEvent
from verify_pipeline import stop_process

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio", action="store_true", help="Capture a clean, real demo and screenshots instead of the test suite")
    args = parser.parse_args()
    node = shutil.which("node")
    cli = ROOT/"node_modules/@playwright/test/cli.js"
    if node is None or not cli.is_file():
        raise SystemExit("Install Node.js and run npm ci, then npx playwright install chromium")
    source = get_database_url()
    identifier = uuid4().hex
    name = "flowforge_browser_"+identifier
    prefix = name+":"
    admin = create_engine(source, isolation_level="AUTOCOMMIT")
    broker = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    broker.ping()
    processes, logs = [], []
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        with tempfile.TemporaryDirectory(prefix="flowforge_browser_") as directory:
            uploads = Path(directory)/"uploads"
            uploads.mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0))
                port = sock.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            environment = dict(os.environ, POSTGRES_DB=name, UPLOAD_DIR=str(uploads),
                FLOWFORGE_QUEUE=prefix+"jobs", FLOWFORGE_REDIS_PREFIX=prefix,
                FLOWFORGE_BROWSER_URL=url, FLOWFORGE_BROWSER_TESTS="1")
            subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=environment,check=True,capture_output=True)
            isolated = create_engine(source.set(database=name))
            try:
                # Only fixture creation uses direct DB access. Recovery itself is
                # exercised through real browser clicks, dispatcher, and worker.
                with Session(isolated) as session, session.begin():
                    for device in (() if args.portfolio else ("desktop","mobile")):
                        for stage in ("processing","delivery"):
                            filename = uuid4().hex+".csv"
                            (uploads/filename).write_bytes((ROOT/"sample_data/sales.csv").read_bytes())
                            job = Job(name=f"{stage.title()} recovery {device}",job_type="csv",
                                status="failed" if stage=="processing" else "pending",
                                failure_code="processing" if stage=="processing" else None)
                            job.csv_analysis = CsvAnalysis(storage_filename=filename,
                                error_message="private fixture detail must not be rendered")
                            session.add(job)
                            session.flush()
                            session.add(OutboxEvent(job_id=job.id,
                                publication_status="published" if stage=="processing" else "failed",
                                retry_count=0 if stage=="processing" else 5))
            finally:
                isolated.dispose()
            try:
                for index, command in enumerate([
                    ["-m","uvicorn","main:app","--port",str(port)],
                    ["-m","celery","-A","celery_app:celery_app","worker","--pool=solo","--loglevel=warning","--without-gossip","--without-mingle","--without-heartbeat"],
                    ["-m","dispatcher","--interval","0.1"],
                ]):
                    log = open(Path(directory)/f"process-{index}.log","w+",encoding="utf-8")
                    logs.append(log)
                    processes.append(subprocess.Popen([sys.executable,*command],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT))
                deadline=time.monotonic()+30
                with httpx.Client(timeout=1) as client:
                    while True:
                        try:
                            if client.get(url+"/health/ready").status_code==200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic()>=deadline:
                            raise TimeoutError("Browser-test app did not start")
                        time.sleep(.2)
                command = [node, str(ROOT/"scripts/capture_portfolio.cjs")] if args.portfolio else [node,str(cli),"test"]
                runner=subprocess.Popen(command,cwd=ROOT,env=environment)
                processes.append(runner)
                code=runner.wait(timeout=240)
                if code:
                    raise subprocess.CalledProcessError(code,command)
                print("PASS: portfolio capture completed against isolated resources" if args.portfolio else "PASS: browser workflows completed against isolated resources")
            except Exception:
                for index,log in enumerate(logs):
                    log.flush();log.seek(0)
                    print(f"Isolated app process {index}:\n{log.read()[-3000:]}",file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    stop_process(process)
                for log in logs:
                    log.close()
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
        for key in broker.scan_iter(match=prefix+"*",count=100):
            broker.delete(key)
        broker.close()

if __name__=="__main__":
    main()
