"""Transitions shared by the API and workers."""

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}
