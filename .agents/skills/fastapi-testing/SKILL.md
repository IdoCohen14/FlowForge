---
name: fastapi-testing
description: A reusable workflow for writing, reviewing, and running FastAPI tests in FlowForge.
---

# FastAPI testing for FlowForge

## When to use

Use this skill when adding API endpoints, changing existing API behavior, fixing backend bugs, or improving backend test coverage in FlowForge.

## Workflow

1. Read the root `AGENTS.md`, relevant application code, and existing tests before making changes. The current application is in `main.py`; job tests are in `tests/test_jobs.py`.
2. Identify success cases, validation errors, and relevant edge cases. For jobs, consider required request fields, UUID uniqueness, initial status, retrieval, populated and empty lists, and missing IDs. Preserve existing API behavior unless the user explicitly requests a change; job creation currently returns HTTP 200, and missing jobs return HTTP 404 with `{"detail": "Job not found"}`.
3. Write focused `test_*` functions using pytest and `fastapi.testclient.TestClient`. Follow existing typed test functions and fixtures. Reuse the existing `client` fixture when adding tests to `test_jobs.py`: it clears `main.jobs` before each test and in `finally` afterward. Ensure equivalent isolation for tests in other files, including cleanup when a test fails. Create test data within each test; never depend on execution order or a live server.
4. Assert response status codes and meaningful response data. Check dynamically generated IDs for validity and uniqueness rather than hardcoding them. Test validation behavior supported by the actual Pydantic model; avoid brittle assertions on complete framework-generated error messages.
5. From the project root, run the complete suite using the existing Windows virtual environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -v
   ```

   A focused test run may help during development, but run the full suite before reporting completion. TestClient runs the API in process; the local server on port 8080 is not needed.
6. Review failures against the intended API contract. Correct test implementation mistakes without changing application behavior to satisfy an incorrect assertion. Fix application code only within the user's authorized scope. Rerun relevant checks after fixes.
7. Report the executed command, passing and failing counts, warnings, and any unrun checks accurately. Never claim tests passed unless they were executed. Explain important changes and engineering decisions concisely.

## Conventions and scope

- Keep Python clean, maintainable, beginner-friendly, and typed; keep changes focused on the requested functionality.
- Prefer existing dependencies and simple fixtures. Avoid unnecessary abstractions or shared test infrastructure.
- Do not modify unrelated files. Do not install dependencies, commit, push, or run destructive commands without permission; this skill grants no additional authorization.
- If required packages are missing, report the missing dependency and obtain permission before installing it. Do not suppress warnings or add dependencies merely to eliminate them.
