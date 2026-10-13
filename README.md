# FlowForge

FlowForge is a software engineering portfolio project evolving into a distributed task processing platform. Its current foundation is a FastAPI service for creating and retrieving jobs, supported by automated API tests.

Jobs are persisted in PostgreSQL through SQLAlchemy, with schema migrations managed by Alembic. A transactional outbox dispatcher publishes jobs through Redis to Celery workers. Continuous dispatch, crash recovery, and a React interface are planned.

## Current features

- Create jobs with unique UUID identifiers and an initial `pending` status.
- Retrieve individual jobs and list all jobs.
- Validate job creation requests with Pydantic.
- Return a clear HTTP 404 response for missing jobs.
- Update job status with validated lifecycle transitions.
- Exercise the API with isolated tests using pytest and FastAPI's TestClient.
- Explore the API through FastAPI's generated documentation.

Jobs survive FastAPI restarts and are shared by server processes connected to the same database. Each request uses a SQLAlchemy session: writes commit, failures roll back, and sessions close after use. Status updates lock the job row during transition validation. Celery workers execute a deterministic demonstration operation; job deletion is not implemented.

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
| Docker Compose | Local PostgreSQL and Redis services with persistent volumes |
| Redis 7 | Celery broker and temporary task results |
| Celery | Background job processing and an addition demonstration task |

| Planned technology | Intended purpose |
| --- | --- |
| Continuous dispatch and recovery | Scheduled outbox polling and recovery of interrupted jobs |
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

## Local Redis infrastructure

The `redis` service uses the official `redis:7` image, pinned to major version 7. Celery uses it for job processing and the standalone addition task. Job publication is performed by the outbox dispatcher.

With Docker Desktop running, validate the configuration, start only Redis, and verify readiness:

```powershell
docker compose config --quiet
docker compose up -d --no-deps redis
docker compose ps redis
docker compose exec redis redis-cli ping
```

The ping command should return `PONG`. Redis is published only on `127.0.0.1:6379` for local development. The service has a `redis-cli ping` health check.

`.env.example` includes `REDIS_URL=redis://127.0.0.1:6379/0` for future workers running on Windows. Add it to your existing local `.env` when needed; do not replace existing credentials. Workers running in the same Compose network will instead use `redis://redis:6379/0`, where `redis` is the service hostname. Future producers will enqueue tasks through Redis and Celery workers will consume them; PostgreSQL remains the source of persisted job records.

Append-only persistence is enabled, with writes synchronized every second, and the named `redis_data` volume stores files at `/data`. This preserves broker data across container recreation for local development, though it does not guarantee task completion or prevent loss of the most recent writes after a crash. The existing PostgreSQL service and `postgres_data` volume are unchanged. See the [official Redis image documentation](https://hub.docker.com/_/redis).

## Celery demonstration worker

`celery_app.py` loads `REDIS_URL` from the environment or existing `.env`, defaulting to `redis://127.0.0.1:6379/0` for Windows processes. Environment variables take precedence. `tasks.py` registers an `add` task, separate from the FastAPI endpoints. Redis carries queued task messages and stores results, which expire after one hour. No tasks are automatically dispatched when jobs are created.

Install the declared dependencies during initial setup with `python -m pip install -r requirements.txt` using the virtual environment, as shown above. The `celery[redis]` dependency includes the required Redis Python client.

With Redis running, start a worker from the project root in a PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe -m celery -A celery_app:celery_app worker --pool=solo --loglevel=info
```

The solo pool executes one task at a time. Celery does not officially support Windows; this command is intended for local development. See the [Celery platform notes](https://docs.celeryq.dev/en/stable/getting-started/introduction.html).

In a second PowerShell terminal, enqueue the demonstration task and wait up to ten seconds for its result:

```powershell
.\.venv\Scripts\python.exe -c "from tasks import add; result = add.delay(2, 3); print('Task ID:', result.id); print('Result:', result.get(timeout=10))"
```

The expected result is `5`. Stop the worker with Ctrl+C. The addition task does not access PostgreSQL; `process_job` reads and updates PostgreSQL jobs.

To test the task without Redis or a worker:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tasks.py::test_add_task -v
```

This addition test uses Celery's local `apply` method. Job-worker tests and the complete suite require PostgreSQL and use temporary test databases.

## Transactional outbox dispatcher

Job creation stores a `process_job` outbox event in the same PostgreSQL transaction as the job. It does not publish directly to Redis. Apply migrations with `.\.venv\Scripts\python.exe -m alembic upgrade head` before using the dispatcher.

`dispatcher.dispatch_batch(engine, batch_size=20, max_attempts=5, base_delay_seconds=5, max_delay_seconds=300)` runs one bounded batch. It selects due pending events in creation order using PostgreSQL `FOR UPDATE SKIP LOCKED` and holds their row locks until the batch transaction completes. Simultaneous dispatcher instances skip claimed rows.

Messages contain the job UUID as a string argument to `tasks.process_job`. The event becomes `published` only after Celery publication succeeds. On failure, `retry_count` increments and `next_attempt_at` schedules a retry with exponential delay capped at `max_delay_seconds`. Events reaching `max_attempts` remain stored with publication status `failed`. Failed attempts are logged without broker exception details. Subsequent batch invocations perform retries; this module does not run a continuous loop.

Publication and PostgreSQL commits are separate operations. A crash or database failure after publication may result in duplicate delivery on the next run. Future processing tasks must therefore handle duplicates safely. `published` means accepted by the publisher, not executed successfully by a worker. A failed database transaction can also roll back retry accounting. Row locks remain held during broker calls, so small batches are appropriate; the batch size does not impose a network time limit.

`process_job(job_id)` now locks the PostgreSQL row and claims only `pending` jobs by committing `running`. It computes a deterministic character checksum of the job name outside the claim transaction, then uses a fresh locked transaction to change `running` to `completed` or `failed`. The checksum is a demonstration only and is not stored or added to the API response. Processing errors are logged and re-raised so Celery also records task failure.

Duplicate deliveries skip missing, running, completed, and failed jobs. Finalization preserves terminal states set by another request. The API and worker share the same transition rules. Tests execute tasks locally against temporary databases and mock dispatcher publication; they never send messages to Redis or start workers.

Recovery is intentionally limited: a worker crash after claiming a job, or a database failure while recording completion, can leave it `running`. A duplicate delivery will skip it. There is no lease, stale-job recovery, or automatic retry of processing yet. A pending job manually moved to `running` is also skipped by workers. These guards prevent duplicate execution but do not provide exactly-once processing or guaranteed completion.

## Development guidelines

See [AGENTS.md](AGENTS.md) for the project's engineering conventions, permission requirements, and environment instructions. Keep changes focused, preserve API behavior, and add tests for new functionality.
