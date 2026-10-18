"""Real API/dispatcher/Redis/worker verification with isolated resources.

Creates and removes only a generated database, temporary upload directory, and
UUID-prefixed Redis keys. Never starts a consumer of the development queue.
"""
import json
import os
from pathlib import Path
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
from database import get_database_url

def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Restrict termination to this harness's own process tree.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

def main():
    source_url = get_database_url()
    identifier = uuid4().hex
    database = "flowforge_verify_" + identifier
    prefix = "flowforge_verify_" + identifier + ":"
    admin = create_engine(source_url, isolation_level="AUTOCOMMIT")
    broker = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    broker.ping()
    processes = []
    logs = []
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        created = True
        with tempfile.TemporaryDirectory(prefix="flowforge_verify_") as directory:
            environment = dict(os.environ, POSTGRES_DB=database,
                UPLOAD_DIR=str(Path(directory)/"uploads"), FLOWFORGE_QUEUE=prefix+"jobs",
                FLOWFORGE_REDIS_PREFIX=prefix)
            # An ephemeral loopback port avoids interfering with the user's server.
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            subprocess.run([sys.executable,"-m","alembic","upgrade","head"], cwd=ROOT, env=environment, check=True, capture_output=True)
            subprocess.run([sys.executable,"-m","alembic","check"], cwd=ROOT, env=environment, check=True, capture_output=True)
            commands = [
                ["-m","uvicorn","main:app","--port",str(port)],
                ["-m","celery","-A","celery_app:celery_app","worker","--pool=solo","--loglevel=warning","--without-gossip","--without-mingle","--without-heartbeat"],
                ["-m","dispatcher","--interval","0.1"],
            ]
            try:
                for index, command in enumerate(commands):
                    log = open(Path(directory)/f"process-{index}.log", "w+", encoding="utf-8")
                    logs.append(log)
                    processes.append(subprocess.Popen([sys.executable,*command], cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT))
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic()+30
                while True:
                    try:
                        if httpx.get(url+"/health/ready", timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Isolated API failed to start")
                    time.sleep(.2)
                subprocess.run([sys.executable,str(ROOT/"scripts/demo.py"),"--url",url], cwd=ROOT, env=environment, check=True)
                # Verify a second delivery preserves the committed results.
                from celery import Celery
                client = Celery(broker=environment.get("REDIS_URL", "redis://127.0.0.1:6379/0"), backend=environment.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
                client.conf.broker_transport_options = {"global_keyprefix": prefix}
                client.conf.result_backend_transport_options = {"global_keyprefix": prefix}
                job = httpx.get(url+"/jobs").json()[0]
                before = httpx.get(url+f"/jobs/{job['id']}/analysis").json()
                duplicate = client.send_task("tasks.process_job", args=[job["id"]], queue=prefix+"jobs")
                duplicate.get(timeout=30)
                assert httpx.get(url+f"/jobs/{job['id']}/analysis").json() == before
                client.close()
                # Pause only this isolated worker's analyzer, then kill after the
                # durable claim. The restarted worker runs the unmodified analyzer.
                stop_process(processes[1])
                pause_code = (
                    "import tasks,time; original=tasks.analyze_csv; "
                    "tasks.analyze_csv=lambda path:(time.sleep(60),original(path))[1]; "
                    "tasks.celery_app.worker_main(['worker','--pool=solo','--loglevel=warning',"
                    "'--without-gossip','--without-mingle','--without-heartbeat'])"
                )
                paused_log = open(Path(directory)/"paused-worker.log", "w+", encoding="utf-8")
                logs.append(paused_log)
                paused = subprocess.Popen([sys.executable,"-c",pause_code], cwd=ROOT, env=environment, stdout=paused_log, stderr=subprocess.STDOUT)
                processes.append(paused)
                with (ROOT/"sample_data/sales.csv").open("rb") as source:
                    response = httpx.post(url+"/jobs/csv", files={"file": ("sales.csv",source)}, data={"name":"Crash recovery verification"})
                response.raise_for_status()
                interrupted = response.json()["id"]
                deadline = time.monotonic()+30
                while httpx.get(url+f"/jobs/{interrupted}").json()["status"] != "running":
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Paused worker never claimed the verification job")
                    time.sleep(.1)
                stop_process(paused)
                isolated = create_engine(source_url.set(database=database))
                try:
                    from sqlalchemy import text
                    with isolated.begin() as connection:
                        connection.execute(text("UPDATE jobs SET lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=:id"), {"id":interrupted})
                    restart_log = open(Path(directory)/"restarted-worker.log", "w+", encoding="utf-8")
                    logs.append(restart_log)
                    processes.append(subprocess.Popen([sys.executable,*commands[1]], cwd=ROOT, env=environment, stdout=restart_log, stderr=subprocess.STDOUT))
                    deadline = time.monotonic()+30
                    while True:
                        recovered = httpx.get(url+f"/jobs/{interrupted}/analysis").json()
                        if recovered["status"] == "completed":
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Killed worker's job did not recover")
                        time.sleep(.1)
                    assert recovered["results"] == before["results"]
                    with isolated.connect() as connection:
                        assert connection.execute(text("SELECT attempt_count FROM jobs WHERE id=:id"), {"id":interrupted}).scalar_one() == 2
                finally:
                    isolated.dispose()
                print("PASS: killed worker recovered and completed on attempt 2.")
                print("PASS: real HTTP pipeline, schema consistency, and duplicate delivery; isolated resources cleaned up.")
            except Exception:
                for index, log in enumerate(logs):
                    log.flush()
                    log.seek(0)
                    # Logs refer only to generated verification resources.
                    print(f"Process {index}:\n{log.read()[-4000:]}", file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    stop_process(process)
                for log in logs:
                    log.close()
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()
        # Never FLUSHDB: delete only this run's unguessable prefix, in small batches.
        for key in broker.scan_iter(match=prefix+"*", count=100):
            broker.delete(key)
        broker.close()

if __name__ == "__main__":
    main()
