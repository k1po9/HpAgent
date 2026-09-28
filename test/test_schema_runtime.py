from hashlib import sha256

import pytest

from persistence.migrate import SchemaVerificationError, migrate, verify_schema


def test_migration_source_errors_precede_database_access(tmp_path, monkeypatch):
    root = tmp_path / "migrations"
    monkeypatch.setenv("HPAGENT_MIGRATIONS_DIR", str(root))
    with pytest.raises(SchemaVerificationError, match="migration_directory_missing"):
        verify_schema("invalid database URL")
    with pytest.raises(SchemaVerificationError, match="migration_directory_missing"):
        migrate("invalid database URL")

    root.mkdir()
    with pytest.raises(SchemaVerificationError, match="migration_directory_empty"):
        verify_schema("invalid database URL")
    with pytest.raises(SchemaVerificationError, match="migration_directory_empty"):
        migrate("invalid database URL")


def test_verify_schema_reports_real_mismatch(tmp_path, monkeypatch):
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "001.sql").write_text("SELECT 1;")
    monkeypatch.setenv("HPAGENT_MIGRATIONS_DIR", str(root))

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, _):
            pass

        def fetchall(self):
            return [("002.sql", sha256(b"SELECT 2;").hexdigest())]

    class Connection:
        def cursor(self):
            return Cursor()

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_):
            pass

    class Pool:
        def connection(self):
            return Context()

    with pytest.raises(SchemaVerificationError) as error:
        verify_schema(Pool())
    message = str(error.value)
    assert "expected_count=1 applied_count=1" in message
    assert "missing=['001.sql']" in message
    assert "extra=['002.sql']" in message
    assert "changed=[]" in message
