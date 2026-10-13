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