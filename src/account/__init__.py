"""Unified account identity backed by PostgreSQL identity bindings."""

from .models import Account
from .postgres_account_service import PostgresAccountService
from .validation import validate_unified_account_backend

__all__ = ["Account", "PostgresAccountService", "validate_unified_account_backend"]
