# FlowForge — portfolio and interview kit

## Project entry

**FlowForge | Background CSV Analysis Platform**

Python · FastAPI · PostgreSQL · SQLAlchemy/Alembic · Redis · Celery · Docker Compose · JavaScript · Playwright · GitHub Actions

Repository: [FlowForge](https://github.com/IdoCohen14/FlowForge)

The repository remains private. Give reviewers access when sharing the project; the link alone does not grant access.

FlowForge turns validated CSV uploads into durable background analysis jobs. A dataset-focused dashboard shows previews, job status, missing values, numeric statistics, processing timing, and recovery actions. Its main engineering focus is handling the gaps between database transactions, message delivery, and worker execution.

## Resume bullets

Use two or three bullets under a personal-project entry. These describe implemented features, without implying commercial deployment or a team role.

- Built a containerized CSV analysis platform with FastAPI, PostgreSQL, Redis, and Celery, supporting validated uploads, background processing, and an interactive results dashboard.
- Implemented a transactional outbox, exponential publication retries, and worker leases with token checks to recover interrupted jobs and tolerate duplicate delivery while preserving job history.
- Automated verification with 183 Python backend tests, four JavaScript unit tests, and 12 desktop/mobile Chromium workflows in GitHub Actions, covering uploads, recovery, precision, themes, and JSON export.

Shorter version when space is limited:

- Built FlowForge, a FastAPI/PostgreSQL CSV analysis platform with Redis/Celery workers, a transactional outbox, crash recovery with stale-worker protection, and automated backend/browser testing.

## A 45-second introduction

“FlowForge turns CSV uploads into background analysis jobs. Users preview a file, submit it, and inspect statistics in the dashboard. The main challenge was reliability across PostgreSQL, Redis, and Celery. A transactional outbox saves each job with its publication intent. Workers claim jobs with expiring tokens, and recovery replaces interrupted claims without letting stale workers overwrite results. I tested the workflow with isolated databases, actual worker crashes, and desktop/mobile browser tests. It is a single-host portfolio project, with documented limits around authentication, long-running jobs, and broker data loss. It does not claim exactly-once execution.”

## Demo walkthrough

The included video is a silent recording of actual application interactions using the bundled four-row sales sample, a real API, dispatcher, Redis broker, worker, and PostgreSQL database. It uses a disposable workspace rather than a running development user's records. Processing is not mocked and pauses are presentation pacing.

1. Introduce the empty workspace and the CSV upload panel.
2. Select `sales.csv` and show the four-row preview. Explain that preview validates the file without creating a job.
3. Name the analysis **Sales performance** and create it. Show the completed status and recorded attempt timing.
4. Point out four rows, three columns, no missing values, Price mean **401.25**, and Quantity mean **5**.
5. Search for **Price**, then clear the filter and sort numeric means. Explain that sorting preserves Decimal-string precision.
6. Switch to data quality and JSON, then export the complete results.
7. Toggle light/dark mode and finish on the dark dataset view.

For a live demonstration, run `scripts/demo.py` or use the dashboard. For repeatable captures, use the isolated harness described in the README.

## Architecture to explain

Browser → FastAPI → PostgreSQL job + CSV metadata + outbox event → dispatcher → Redis → Celery → PostgreSQL results → dashboard.

The uploaded CSV lives in shared storage, separate from PostgreSQL. API and worker use the same upload directory or Docker volume. They do not exchange file bytes through the broker; the task message contains a job UUID.

The API's job and outbox writes commit together. The dispatcher publishes bounded batches using `FOR UPDATE SKIP LOCKED`. The worker claims a pending job under a row lock, records an attempt and a five-minute lease, computes outside the claim transaction, and finalizes only if its token still matches. Results and completion commit together.

## Interview questions and answers

### Why a transactional outbox?

PostgreSQL and Redis do not share one transaction. Publishing directly after committing a job can leave a persisted job with no message if the process crashes. Publishing before the database commit can send a message for a job that never commits. Recording the job and an outbox event in one transaction makes the intent durable; a separate dispatcher performs publication.

The remaining gap is between broker publication and marking an event published. A crash in that window can produce another delivery. The worker must handle duplicates, so the outbox does not imply exactly-once execution.

### What happens if a worker crashes?

A committed claim has a five-minute lease. The continuously running dispatcher scans expired running jobs and atomically clears the old claim and creates another outbox event. After three claimed attempts, expiry marks the job failed. A new claim uses a fresh token; an earlier worker that later finishes cannot save results for the new claim.

There is no heartbeat renewal or forced cancellation. A slow live worker may overlap with its replacement after expiry. The calculation is repeatable and has no external side effects. Payments or email delivery would need their own idempotency mechanism.

### How do locks and tokens differ?

Row locks serialize the short claim and finalization transactions. They are released during analysis so a worker does not hold a database transaction while reading a file. The persisted token links completion to the current claim across those transactions. `SKIP LOCKED` lets other dispatchers/recovery instances take eligible rows without waiting on claimed rows.

### Why keep results and status in one transaction?

It prevents a committed completed job from appearing before its results are stored. The results endpoint also reads status and analysis together in a joined query. A database failure while finalizing leaves the claim recoverable rather than pretending completion succeeded.

### How is CSV upload handled?

Files have generated UUID storage names, a 10 MiB file limit, and an 11 MiB total request cap. The parser validates UTF-8/BOM content, unique nonempty headers, row width, null characters, column count, and field size. Client filenames do not become storage paths; MIME type alone is not trusted. The worker revalidates stored content.

Preview validates the full file but returns only five rows, at most eight columns, and at most 200 characters per displayed cell. It deletes its temporary stored file and creates no job. Creation validates again.

### Why Decimal strings?

The analyzer uses Decimal arithmetic with 50 significant digits. Returning numeric statistics as strings prevents an additional precision loss through JSON floats or JavaScript numbers. The dashboard also compares decimal strings when sorting means. Repeating means and values beyond the arithmetic precision are rounded; this is not arbitrary-precision exact statistics.

### What makes retry safe?

Processing retry creates one linked child job referencing the existing file, leaving the original failure intact. A lock on the original and a unique `retry_of` constraint make concurrent/repeated requests return the same child. New metadata and the child's outbox event commit together. An invalid or missing file requires a new upload. Delivery retry instead resets only the latest exhausted outbox event for the same job.

### What do the tests prove?

- Backend tests use generated PostgreSQL databases and temporary upload directories, including concurrency, transaction rollback, stale claims, validation and retry lineage.
- Four frontend unit tests cover precise decimal comparison, filtering and stable sorting.
- Twelve Chromium workflows run at desktop and mobile-emulation sizes against a separate real API/dispatcher/worker stack. They cover preview/upload, sample data, invalid files, filters, tabs, export, recovery, theme persistence and blocked storage.
- A separate real pipeline verifier kills a claimed worker and checks recovery, duplicate delivery, and migration/schema consistency. It advances only the isolated test lease instead of waiting five minutes.

These checks are not a proof of zero bugs, physical-device compatibility, every browser engine, or distributed capacity.

### What performance claim can you support?

The recorded local analyzer benchmark processed a deterministic 100,000-row, three-column, 1,779,024-byte CSV in a median 0.259764 seconds across three runs on Windows/Python 3.14.6. That is about 384,965 rows/second for the analyzer on that machine. It excludes HTTP upload, upload-time validation, queue wait, dispatcher polling, and database writes. It is not an end-to-end throughput guarantee or a comparison with another product. Re-run `scripts/benchmark.py` for new measurements.

### What remains before public production use?

Authentication and authorization; quotas and rate limits; TLS; monitoring/alerts; backup and restore procedures; file retention; pagination/indexing improvements at larger scale; and lease renewal or cancellation for long jobs. The current readiness endpoint checks database connectivity, not worker progress. Redis loss after a successful publication can strand a pending job because published events are not automatically reconciled.

The project is a single-host portfolio deployment. Celery's Windows solo worker is a local convenience; Linux application containers are the preferred runtime. Broker delivery is at least once and computation may overlap. Do not describe it as exactly once, highly available, multi-tenant, or proven at large scale.

## Before an interview

- Practice the introduction, then explain the outbox failure window without reading notes.
- Be able to trace one upload through `main.py`, `dispatcher.py`, `tasks.py`, and `recovery.py`.
- Review one concurrency test, one rollback test, and the browser recovery test.
- Explain why token fencing protects database finalization but does not prevent overlapping computation.
- Run the sample locally and know how to inspect API/worker/dispatcher logs.
- Use the limitations as deliberate scope boundaries; do not invent users, business impact, uptime, team leadership, or benchmark improvements.

## Evidence and sharing

- README: setup, architecture, guarantees, limitations, and screenshots.
- `docs/verification.md`: recorded local verification and benchmark conditions.
- `docs/media/flowforge-demo.webm`: real silent walkthrough.
- `docs/media/dashboard-dark.png`, `dashboard-light.png`, `dashboard-mobile.png`: actual application screenshots with synthetic sample data.
- GitHub Actions: repeatable backend, frontend, real pipeline, and browser checks.

The assets do not contain credentials or live development records. No repository visibility or sharing permissions were changed as part of packaging.
