# FlowForge agent instructions

## Project context

FlowForge is a software engineering portfolio project that will evolve into a distributed task processing platform.

The current application is defined in `main.py`. It uses FastAPI, a Pydantic request model, UUID job identifiers, and an in-memory dictionary for job storage. Jobs are created with a `pending` status; task execution and persistent storage are not implemented. Stored jobs are lost when the process restarts and are not shared between processes.

## Engineering guidelines

- Write clean, maintainable, beginner-friendly Python code.
- Follow Python conventions and use type hints.
- Keep changes minimal and avoid unnecessary complexity.
- Explain important engineering decisions concisely.
- Do not modify unrelated files.
- Preserve existing API functionality.
- Add automated tests for new functionality.
- Never claim tests passed unless they were executed. Clearly state when tests were not run.
- Do not install dependencies, commit, push, or run destructive commands without the user's permission.

## Development environment

- Use Windows and VS Code.
- Use the project's `.venv` Python environment when running Python tools or tests.
- The local FastAPI server uses port `8080`.
- Planned technologies include PostgreSQL, Redis, Celery, React, Docker, and Pytest. Treat these as planned additions, not existing implementations.

## Current API

| Method | Path | Current behavior |
| --- | --- | --- |
| GET | `/` | Returns `{"message": "Welcome to FlowForge!"}`. |
| POST | `/jobs` | Accepts a JSON body with a required `name` string. Generates a UUID and stores and returns a job containing string `id`, supplied `name`, and `status` set to `pending`. Currently returns HTTP 200 on success. |
| GET | `/jobs` | Returns all stored jobs as a JSON array, or an empty array when no jobs exist. |
| GET | `/jobs/{job_id}` | Accepts a string job ID and returns the matching job. Returns HTTP 404 with `{"detail": "Job not found"}` when the ID is absent. |

The `JobCreate` Pydantic model validates job creation requests. All current successful endpoints return HTTP 200. There are no endpoints for updating, deleting, or executing jobs.
