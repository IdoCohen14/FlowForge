from typing import Literal

from datetime import timedelta
from pathlib import Path
from fastapi.responses import FileResponse, JSONResponse
from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_session
from models import CsvAnalysis, Job, OutboxEvent
from csv_analysis import CsvValidationError
from file_storage import FileStorageError, UnsupportedFileType, UploadTooLarge, resolve_storage_path, store_csv
from job_lifecycle import ALLOWED_TRANSITIONS

app = FastAPI(title="FlowForge", version="1.0.0", description="Durable background CSV analysis with a transactional outbox and bounded worker recovery.")


class UploadBodyLimit:
    """Bound the entire multipart body, including chunked uploads, before spooling."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/jobs/csv" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        limit = 11 * 1024 * 1024  # 10 MiB file plus bounded multipart overhead.
        size = 0
        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise HTTPException(status_code=413, detail="Upload request exceeds the 11 MiB limit")
            return message
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    too_large = int(value) > limit
                except ValueError:
                    return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                if too_large:
                    return await JSONResponse({"detail": "Upload request exceeds the 11 MiB limit"}, status_code=413)(scope, receive, send)
        await self.app(scope, limited_receive, send)


app.add_middleware(UploadBodyLimit)


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/health/ready")
def readiness(session: Session = Depends(get_session)):
    try:
        session.execute(select(1))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ready"}

def job_response(job: Job) -> dict[str, str]:
    return {"id": str(job.id), "name": job.name, "status": job.status}


def parse_job_id(job_id: str) -> UUID:
    try:
        parsed = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job not found")
    if str(parsed) != job_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return parsed


@app.get("/")
def home():
    return {"message": "Welcome to FlowForge!"}


class JobCreate(BaseModel):
    name: str


class JobStatusUpdate(BaseModel):
    status: Literal["pending", "running", "completed", "failed"]


class NumericStatistics(BaseModel):
    count: int
    minimum: str
    maximum: str
    mean: str


class ColumnAnalysis(BaseModel):
    name: str
    missing_values: int
    numeric: NumericStatistics | None


class AnalysisResults(BaseModel):
    row_count: int
    column_count: int
    headers: list[str]
    missing_values_total: int
    columns: list[ColumnAnalysis]


class JobAnalysisResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed"]
    results: AnalysisResults | None = None
    error_message: str | None = None


class ErrorResponse(BaseModel):
    detail: str


@app.post("/jobs")
def create_job(job: JobCreate, session: Session = Depends(get_session)) -> dict[str, str]:
    stored = Job(name=job.name)
    session.add(stored)
    # Flush obtains the UUID but does not commit the job independently.
    session.flush()
    session.add(OutboxEvent(job_id=stored.id))
    session.commit()
    return job_response(stored)


@app.post("/jobs/csv")
def create_csv_job(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    try:
        filename = store_csv(file.file, file.filename or "")
    except UploadTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from None
    except UnsupportedFileType as error:
        raise HTTPException(status_code=415, detail=str(error)) from None
    except CsvValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except FileStorageError:
        raise HTTPException(status_code=500, detail="Could not store CSV file") from None

    saved_path = resolve_storage_path(filename)
    try:
        stored = Job(name=name if name is not None else "CSV analysis", job_type="csv")
        stored.csv_analysis = CsvAnalysis(storage_filename=filename)
        session.add(stored)
        session.flush()
        session.add(OutboxEvent(job_id=stored.id))
        session.commit()
    except Exception:
        try:
            session.rollback()
        finally:
            saved_path.unlink(missing_ok=True)
        raise
    return job_response(stored)


@app.get("/jobs")
def list_jobs(session: Session = Depends(get_session)) -> list[dict[str, str]]:
    return [job_response(job) for job in session.scalars(select(Job))]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, str]:
    job = session.get(Job, parse_job_id(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_response(job)


@app.get(
    "/jobs/{job_id}/analysis",
    response_model=JobAnalysisResponse,
    responses={
        200: {"description": "Completed results, or failed status with a sanitized error"},
        202: {"model": JobAnalysisResponse, "description": "CSV job pending or running; results are null"},
        404: {"model": ErrorResponse, "description": "Job not found or invalid job ID"},
        409: {"model": ErrorResponse, "description": "Not a CSV job, or completed analysis unavailable"},
    },
)
def get_job_analysis(
    job_id: str, response: Response, session: Session = Depends(get_session)
) -> JobAnalysisResponse:
    # One joined statement reads job status and results from the same snapshot.
    record = session.execute(
        select(Job.status, Job.job_type, CsvAnalysis.results)
        .outerjoin(CsvAnalysis, CsvAnalysis.job_id == Job.id)
        .where(Job.id == parse_job_id(job_id))
    ).one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if record.job_type != "csv":
        raise HTTPException(status_code=409, detail="Job is not a CSV job")
    if record.status in ("pending", "running"):
        response.status_code = 202
        return JobAnalysisResponse(id=job_id, status=record.status)
    if record.status == "failed":
        # Do not expose arbitrary stored errors, file paths, or database details.
        return JobAnalysisResponse(id=job_id, status="failed", error_message="CSV processing failed")
    if record.results is None:
        raise HTTPException(status_code=409, detail="Completed CSV analysis is unavailable")
    return JobAnalysisResponse(id=job_id, status="completed", results=record.results)


@app.patch("/jobs/{job_id}/status")
def update_job_status(
    job_id: str, update: JobStatusUpdate, session: Session = Depends(get_session)
) -> dict[str, str]:
    job = session.scalar(select(Job).where(Job.id == parse_job_id(job_id)).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.job_type == "csv" or job.claim_token is not None:
        raise HTTPException(status_code=409, detail="Worker manages this job's status")
    if update.status not in ALLOWED_TRANSITIONS[job.status]:
        raise HTTPException(status_code=409, detail="Invalid status transition")

    job.status = update.status
    if update.status == "running":
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=5)
    else:
        job.lease_expires_at = None
    session.commit()
    return job_response(job)
