from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import Job

app = FastAPI()

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


@app.post("/jobs")
def create_job(job: JobCreate, session: Session = Depends(get_session)) -> dict[str, str]:
    stored = Job(name=job.name)
    session.add(stored)
    session.commit()
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


@app.patch("/jobs/{job_id}/status")
def update_job_status(
    job_id: str, update: JobStatusUpdate, session: Session = Depends(get_session)
) -> dict[str, str]:
    job = session.scalar(select(Job).where(Job.id == parse_job_id(job_id)).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    allowed_transitions = {
        "pending": {"running"},
        "running": {"completed", "failed"},
        "completed": set(),
        "failed": set(),
    }
    if update.status not in allowed_transitions[job.status]:
        raise HTTPException(status_code=409, detail="Invalid status transition")

    job.status = update.status
    session.commit()
    return job_response(job)
