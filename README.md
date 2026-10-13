# FlowForge

FlowForge is a software engineering portfolio project evolving into a distributed task processing platform. Its current foundation is a FastAPI service for creating and retrieving jobs, supported by automated API tests.

The planned platform will introduce persistent job storage, queued background processing, worker execution, and a React interface. These capabilities are not implemented yet.

## Current features

- Create jobs with unique UUID identifiers and an initial `pending` status.
- Retrieve individual jobs and list all jobs.
- Validate job creation requests with Pydantic.
- Return a clear HTTP 404 response for missing jobs.
- Exercise the API with six isolated tests using pytest and FastAPI's TestClient.
- Explore the API through FastAPI's generated documentation.

Jobs are stored in an in-memory dictionary. They are lost when the server restarts and are not shared between server processes. Jobs currently remain `pending`; there is no task execution, status update, or deletion functionality.

## Technologies

| Current technology | Purpose |
| --- | --- |
| Python | Application and test code |
| FastAPI | HTTP API and generated documentation |
| Pydantic | Request validation |
| Uvicorn | Local ASGI server |
| pytest | Automated tests |
| HTTPX and FastAPI TestClient | In-process API testing |

| Planned technology | Intended purpose |
| --- | --- |
| PostgreSQL | Persistent job storage |
| Redis | Broker for queued tasks |
| Celery | Background task processing and workers |
| React | User interface |
| Docker | Containerized development and deployment |

## Project structure

```text
FlowForge/
├── AGENTS.md          # Contributor and coding-agent instructions
├── main.py            # FastAPI application and in-memory job storage
├── requirements.txt   # Application and testing dependencies
├── tests/
│   └── test_jobs.py    # API tests with isolated job storage
├── .gitignore
└── README.md
```

## Local setup

Use Windows and VS Code. The current development environment uses Python 3.14. Run the following PowerShell commands from the project root.

Create a virtual environment if `.venv` does not already exist:

```powershell
python -m venv .venv
```

Install application and testing dependencies into that environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Select `.venv\Scripts\python.exe` as the Python interpreter in VS Code. The commands below use the environment directly, so activation is optional.

The requirements list direct dependencies with bounds allowing patch updates within the installed minor versions. Pip resolves their transitive dependencies automatically.

## Run the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8080
```

The API is available at <http://127.0.0.1:8080>. Interactive documentation is available at <http://127.0.0.1:8080/docs>, with alternative documentation at <http://127.0.0.1:8080/redoc>.

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/` | Returns `{"message": "Welcome to FlowForge!"}`. |
| POST | `/jobs` | Accepts `{"name": "Process report"}` and returns a job with `id`, `name`, and `status: "pending"`. |
| GET | `/jobs` | Returns a JSON array of stored jobs, or `[]` when empty. |
| GET | `/jobs/{job_id}` | Returns the matching job, or HTTP 404 with `{"detail": "Job not found"}`. |

All successful endpoints currently return HTTP 200, including job creation.

## Run automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

The six tests cover job creation, unique IDs, job retrieval, populated and empty job lists, and missing-job errors. A fixture clears the in-memory dictionary before and after each test. TestClient calls the application in process; a running server is not required.

The currently installed Starlette version emits a deprecation warning about TestClient's HTTPX integration. This warning does not prevent the existing tests from running.

## Development guidelines

See [AGENTS.md](AGENTS.md) for the project's engineering conventions, permission requirements, and environment instructions. Keep changes focused, preserve API behavior, and add tests for new functionality.
