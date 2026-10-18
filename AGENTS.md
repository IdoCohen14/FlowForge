# FlowForge agent instructions

FlowForge is a FastAPI/PostgreSQL/Redis/Celery CSV analysis portfolio project. Read README.md for current architecture, setup and limitations. The native server port is 8080; use the existing `.venv` for Python commands.

- Write clean, maintainable Python with type hints and focused changes.
- Preserve existing API response formats; document intentional lifecycle restrictions.
- Add meaningful regression tests for new behavior and run appropriate checks. Never claim unexecuted checks passed.
- Preserve `.env`, uploaded files, development jobs, and Docker volumes. Never print secrets.
- Tests use uniquely named temporary PostgreSQL databases. Real pipeline verification must use its own database, upload storage, broker key prefix and queue.
- Never clear development queues or volumes to make tests pass.
- Do not install dependencies, commit, push, or run destructive commands without user authorization. Explicit authorization in the active session applies.
- No exactly-once, high-availability, or public-production claims: state actual guarantees and limitations.
