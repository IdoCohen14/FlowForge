from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uuid import uuid4

app = FastAPI()

jobs = {}


@app.get("/")
def home():
    return {"message": "Welcome to FlowForge!"}


class JobCreate(BaseModel):
    name: str


class JobStatusUpdate(BaseModel):
    status: Literal["pending", "running", "completed", "failed"]


@app.post("/jobs")
def create_job(job: JobCreate):
    job_id = uuid4()

    jobs[str(job_id)] = {
        "id": str(job_id),
        "name": job.name,
        "status": "pending"
    }

    return jobs[str(job_id)]


@app.get("/jobs")
def list_jobs():
    return list(jobs.values())


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    return jobs[job_id]


@app.patch("/jobs/{job_id}/status")
def update_job_status(job_id: str, update: JobStatusUpdate) -> dict[str, str]:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    allowed_transitions = {
        "pending": {"running"},
        "running": {"completed", "failed"},
        "completed": set(),
        "failed": set(),
    }
    if update.status not in allowed_transitions[job["status"]]:
        raise HTTPException(status_code=409, detail="Invalid status transition")

    job["status"] = update.status
    return job
