"""Public job diagnostics and safe, explicit recovery decisions."""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from models import Job, OutboxEvent
from file_storage import FileStorageError, resolve_storage_path

def stored_file_available(job: Job) -> bool:
    if job.csv_analysis is None:
        return False
    try:
        return resolve_storage_path(job.csv_analysis.storage_filename).is_file()
    except (FileStorageError, OSError):
        return False

def describe_job(session: Session, job: Job) -> dict:
    submitted = session.scalar(select(func.min(OutboxEvent.created_at)).where(OutboxEvent.job_id == job.id))
    latest = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job.id)
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc()).limit(1))
    stage = None
    message = None
    action = None
    if job.status == "pending" and latest is not None and latest.publication_status == "failed":
        stage = "delivery"
        message = "Delivery attempts were exhausted. You can try sending this job to the worker again."
        action = "retry_delivery"
    elif job.status == "failed":
        stage = job.failure_code if job.failure_code in {"validation", "storage", "interrupted", "processing"} else "processing"
        if stage == "validation":
            message = "The stored CSV did not pass validation. Correct the file and upload it again."
            action = "upload"
        elif stage == "storage" or (job.job_type == "csv" and not stored_file_available(job)):
            stage = "storage"
            message = "The source file is unavailable. Upload a new copy to analyze it."
            action = "upload"
        else:
            message = ("Processing was interrupted too many times." if stage == "interrupted"
                else "The worker could not complete this analysis.")
            action = "retry_processing" if job.job_type == "csv" else None
    duration = None
    if job.started_at is not None and job.finished_at is not None:
        duration = max(0.0, (job.finished_at-job.started_at).total_seconds())
    retry = session.scalar(select(Job.id).where(Job.retry_of == job.id))
    return {"id": str(job.id), "name": job.name, "status": job.status, "job_type": job.job_type,
        "attempt_count": job.attempt_count, "created_at": submitted, "started_at": job.started_at,
        "finished_at": job.finished_at, "processing_seconds": duration, "failure_stage": stage,
        "message": message, "recovery_action": action, "retry_of": str(job.retry_of) if job.retry_of else None,
        "retry_job_id": str(retry) if retry else None}
