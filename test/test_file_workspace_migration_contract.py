from pathlib import Path


def test_file_workspace_migration_has_required_tables_and_guards() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "persistence/migrations/017_file_workspace_p0.sql"
    ).read_text()
    for table in (
        "stored_files", "message_files", "run_files", "run_budgets",
        "run_usage_ledger",
    ):
        assert f"CREATE TABLE {table}" in migration
    assert "uq_stored_files__scope" in migration
    assert "ct_message_files__shape" in migration
    assert "PRIMARY KEY(run_id,operation_id,dimension)" in migration
    assert "status='ready'" in migration
