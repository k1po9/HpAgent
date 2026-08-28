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


def test_upload_contract_retains_declared_digest_separately() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "persistence/migrations/018_file_upload_contract.sql"
    ).read_text()
    assert "ADD COLUMN declared_sha256" in migration
    assert "^[0-9a-f]{64}$" in migration
    assert "tr_stored_files__api_input_only" in migration
    assert "REVOKE UPDATE ON run_budgets FROM hpagent_api" in migration


def test_orphan_cleanup_index_includes_unbound_ready_files() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "persistence/migrations/019_file_orphan_cleanup.sql"
    ).read_text()
    assert "DROP INDEX IF EXISTS ix_stored_files__cleanup" in migration
    assert "('uploading','ready','rejected','deleted')" in migration
