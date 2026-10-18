from typing import Literal

from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Query, Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_session
from models import CsvAnalysis, Job, OutboxEvent
from csv_analysis import CsvValidationError, iter_validated_csv
from file_storage import FileStorageError, UnsupportedFileType, UploadTooLarge, resolve_storage_path, store_csv
from job_lifecycle import ALLOWED_TRANSITIONS
from job_details import describe_job

app = FastAPI(title="FlowForge", version="1.2.0", description="Durable background CSV analysis with a transactional outbox and bounded worker recovery.")


class UploadBodyLimit:
    """Bound the entire multipart body, including chunked uploads, before spooling."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in ("/jobs/csv", "/jobs/csv/preview") or scope["method"] != "POST":
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
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "static"), name="assets")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")


@app.get("/samples/sales.csv", include_in_schema=False)
def sales_sample() -> FileResponse:
    return FileResponse(Path(__file__).parent / "sample_data" / "sales.csv", media_type="text/csv", filename="sales.csv")


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


def save_csv_upload(file: UploadFile) -> str:
    try:
        return store_csv(file.file, file.filename or "")
    except UploadTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from None
    except UnsupportedFileType as error:
        raise HTTPException(status_code=415, detail=str(error)) from None
    except CsvValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except FileStorageError:
        raise HTTPException(status_code=500, detail="Could not store CSV file") from None



@app.post("/jobs/csv")
def create_csv_job(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    filename = save_csv_upload(file)

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


class DashboardJob(BaseModel):
    id: str
    name: str
    status: str
    job_type: str
    attempt_count: int
    created_at: datetime | None


class DashboardData(BaseModel):
    items: list[DashboardJob]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


@app.get("/dashboard/data", response_model=DashboardData)
def dashboard_data(
    session: Session = Depends(get_session),
    q: str = Query(default="", max_length=200),
    status: Literal["pending", "running", "completed", "failed"] | None = None,
    page: int = Query(default=1, ge=1, le=1000000),
    page_size: int = Query(default=10, ge=1, le=100),
) -> DashboardData:
    """Bounded dashboard queries, preserving the legacy /jobs array contract.

    The earliest outbox event is the submission timestamp, including recovered
    jobs with multiple events. Historical jobs without events have no timestamp.
    """
    counts = {name: 0 for name in ("pending", "running", "completed", "failed")}
    counts.update(dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all()))
    filters = []
    if q.strip():
        filters.append(Job.name.icontains(q.strip(), autoescape=True))
    if status is not None:
        filters.append(Job.status == status)
    total = session.scalar(select(func.count()).select_from(Job).where(*filters))
    submissions = select(
        OutboxEvent.job_id, func.min(OutboxEvent.created_at).label("submitted_at")
    ).group_by(OutboxEvent.job_id).subquery()
    records = session.execute(
        select(Job, submissions.c.submitted_at)
        .outerjoin(submissions, submissions.c.job_id == Job.id)
        .where(*filters)
        .order_by(submissions.c.submitted_at.desc().nullslast(), Job.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return DashboardData(
        items=[DashboardJob(id=str(job.id), name=job.name, status=job.status,
            job_type=job.job_type, attempt_count=job.attempt_count, created_at=submitted)
            for job, submitted in records],
        total=total, page=page, page_size=page_size, counts=counts,
    )


class CsvPreview(BaseModel):
    headers: list[str]
    rows: list[list[str]]
    column_count: int
    has_more_rows: bool
    truncated_cells: bool


@app.post("/jobs/csv/preview", response_model=CsvPreview)
def preview_csv(file: UploadFile = File(...)) -> CsvPreview:
    """Validate a temporary upload and return a bounded, text-only preview."""
    filename = save_csv_upload(file)
    path = resolve_storage_path(filename)
    rows = iter_validated_csv(path)
    try:
        headers = next(rows)
        sample = list(islice(rows, 6))
        displayed = [headers[:8], *[row[:8] for row in sample[:5]]]
        truncated = any(len(cell) > 200 for row in displayed for cell in row)
        return CsvPreview(headers=[cell[:200] for cell in headers[:8]],
            rows=[[cell[:200] for cell in row[:8]] for row in sample[:5]],
            column_count=len(headers), has_more_rows=len(sample)>5, truncated_cells=truncated)
    finally:
        rows.close()
        path.unlink(missing_ok=True)


class JobDetails(BaseModel):
    id: str
    name: str
    status: str
    job_type: str
    attempt_count: int
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    processing_seconds: float | None
    failure_stage: str | None
    message: str | None
    recovery_action: str | None
    retry_of: str | None
    retry_job_id: str | None


@app.get("/jobs/{job_id}/details", response_model=JobDetails)
def get_job_details(job_id: str, session: Session = Depends(get_session)) -> dict:
    job = session.get(Job, parse_job_id(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return describe_job(session, job)


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, str]:
    job = session.scalar(select(Job).where(Job.id == parse_job_id(job_id)).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Locking the original plus the unique retry_of constraint makes repeated
    # clicks return one retry job, preserving the original failed resource.
    existing = session.scalar(select(Job).where(Job.retry_of == job.id))
    if existing is not None:
        return job_response(existing)
    details = describe_job(session, job)
    if details["recovery_action"] == "retry_delivery":
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id)
            .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc()).limit(1).with_for_update())
        if event is None or event.publication_status != "failed":
            raise HTTPException(status_code=409, detail="Delivery state changed; refresh this job")
        event.publication_status = "pending"
        event.retry_count = 0
        event.next_attempt_at = None
        session.commit()
        return job_response(job)
    if details["recovery_action"] != "retry_processing":
        raise HTTPException(status_code=409, detail=details["message"] or "This job is not eligible for retry")
    retry = Job(name=job.name, job_type="csv", retry_of=job.id)
    retry.csv_analysis = CsvAnalysis(storage_filename=job.csv_analysis.storage_filename)
    session.add(retry)
    session.flush()
    session.add(OutboxEvent(job_id=retry.id))
    session.commit()
    return job_response(retry)
