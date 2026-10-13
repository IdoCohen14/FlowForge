# FlowForge

FlowForge is a software engineering portfolio project evolving into a distributed task processing platform. Its current foundation is a FastAPI service for creating and retrieving jobs, supported by automated API tests.

Jobs are persisted in PostgreSQL through SQLAlchemy, with schema migrations managed by Alembic. Queued background processing, worker execution, and a React interface are planned.

## Current features

- Create jobs with unique UUID identifiers and an initial `pending` status.
- Retrieve individual jobs and list all jobs.
- Validate job creation requests with Pydantic.
- Return a clear HTTP 404 response for missing jobs.
- Update job status with validated lifecycle transitions.
- Exercise the API with isolated tests using pytest and FastAPI's TestClient.
- Explore the API through FastAPI's generated documentation.

Jobs survive FastAPI restarts and are shared by server processes connected to the same database. Each request uses a SQLAlchemy session: writes commit, failures roll back, and sessions close after use. Status updates lock the job row during transition validation. Task execution and job deletion are not implemented.

## Technologies

| Current technology | Purpose |
| --- | --- |
| Python | Application and test code |
| FastAPI | HTTP API and generated documentation |
| Pydantic | Request validation |
| Uvicorn | Local ASGI server |
| pytest | Automated tests |
| HTTPX and FastAPI TestClient | In-process API testing |
| PostgreSQL 17 | Persistent job storage |
| SQLAlchemy 2.x and Psycopg 3 | ORM, sessions, and PostgreSQL driver |
| Alembic | Database schema migrations |
| Docker Compose | Local PostgreSQL service and persistent volume |

| Planned technology | Intended purpose |
| --- | --- |
| Redis | Broker for queued tasks |
| Celery | Background task processing and workers |
| React | User interface |
| Docker application containers | Containerized API and worker deployment |

## Project structure

```text
FlowForge/
├── AGENTS.md          # Contributor and coding-agent instructions
├── main.py            # Database-backed FastAPI endpoints
├── database.py        # Environment configuration and request sessions
├── models.py          # SQLAlchemy Job model
├── alembic.ini        # Migration configuration
├── migrations/        # Migration environment and revision scripts
├── compose.yaml       # Local PostgreSQL infrastructure
├── .env.example       # Example development configuration
├── requirements.txt   # Application and testing dependencies
├── tests/
│   ├── conftest.py     # Temporary PostgreSQL databases
│   ├── test_database.py # Configuration and model tests
│   └── test_jobs.py    # API, lifecycle, and rollback tests
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

Start PostgreSQL and apply migrations using the setup below before starting the API.

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
| PATCH | `/jobs/{job_id}/status` | Accepts `{"status": "running"}` and returns the updated job. |

All successful endpoints currently return HTTP 200, including job creation.

Allowed transitions are `pending -> running`, `running -> completed`, and `running -> failed`. Other transitions return HTTP 409. Unsupported statuses or invalid request bodies return HTTP 422. Missing jobs return HTTP 404; malformed job IDs also return 404.

## Run automated tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Run this command for the complete suite. Tests cover creation, validation, retrieval, listing, lifecycle transitions, persistence across sessions, and rollback after database failures. TestClient calls the application in process; a running FastAPI server is not required.

API tests require a running PostgreSQL server and a configured database role with permission to create databases. Each test creates a uniquely named `flowforge_test_<uuid>` database, applies Alembic migrations, and overrides the application's session dependency to use it. Cleanup removes only that database after connections close. Development records and Docker volumes are never cleared. Configuration and model unit tests also run without writing to the development database.

The currently installed Starlette version emits a deprecation warning about TestClient's HTTPX integration. This warning does not prevent the existing tests from running.

## Local PostgreSQL infrastructure

`compose.yaml` defines the PostgreSQL 17 `db` service using the official `postgres:17` image.

Use Docker Desktop with Linux containers and Docker Compose available. From the project root, create `.env` only if it does not exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` to choose local development credentials. Git ignores `.env`; `.env.example` contains example values only. Compose reads `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` from the environment or `.env` and rejects missing or empty values.

Validate the configuration without starting containers:

```powershell
docker compose config --quiet
```

Start PostgreSQL and check its health:

```powershell
docker compose up -d db
docker compose ps
```

PostgreSQL is exposed only at `127.0.0.1:5432`. The health check uses `pg_isready` to check readiness.

The application loads `.env` with python-dotenv. Existing environment variables take precedence. `POSTGRES_HOST` and `POSTGRES_PORT` default to `127.0.0.1` and `5432`; credentials come from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

Apply pending migrations and check schema consistency:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

The initial migration creates `jobs` with a UUID primary key and required `name` and `status` string columns. Run migrations before starting a fresh deployment; the API does not create tables automatically.

Stop PostgreSQL without removing the container:

```powershell
docker compose stop db
```

The Compose-managed named volume `postgres_data` stores database files at `/var/lib/postgresql/data`, the data location for PostgreSQL 17. It retains data across container restarts and recreation. `docker compose down` also retains this named volume; avoid adding `--volumes` if you want to keep the data. Credentials initialize a new database only; editing `.env` does not change credentials in an already initialized volume. See the [official PostgreSQL image documentation](https://hub.docker.com/_/postgres).

## Development guidelines

See [AGENTS.md](AGENTS.md) for the project's engineering conventions, permission requirements, and environment instructions. Keep changes focused, preserve API behavior, and add tests for new functionality.
