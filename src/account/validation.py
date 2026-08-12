"""Shared startup validation for the PostgreSQL identity source."""


def validate_unified_account_backend(worker_database_url: str | None) -> None:
    """Reject startup when the authoritative identity backend is unavailable."""
    if not worker_database_url:
        raise RuntimeError(
            "WORKER_DATABASE_URL is required for the unified account backend"
        )
