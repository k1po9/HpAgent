from pathlib import Path


def test_document_worker_migration_has_owned_idempotent_compact_storage() -> None:
    migration = (
        Path(__file__).parents[1]
        / "persistence/migrations/025_file_document_worker.sql"
    ).read_text()
    assert "CREATE TABLE normalized_documents" in migration
    assert "operation_id varchar(200) NOT NULL UNIQUE" in migration
    assert "FOREIGN KEY(run_id,file_id)" in migration
    assert "REFERENCES run_files(run_id,file_id)" in migration
    assert "GRANT SELECT,INSERT ON normalized_documents TO hpagent_worker" in migration
    assert "GRANT SELECT ON normalized_documents TO hpagent_api" in migration


def test_document_ownership_migration_binds_account_run_and_file() -> None:
    migration = (
        Path(__file__).parents[1]
        / "persistence/migrations/026_file_document_ownership.sql"
    ).read_text()
    assert "UNIQUE(account_id,run_id,file_id)" in migration
    assert "FOREIGN KEY(account_id,run_id,file_id)" in migration
    assert "REFERENCES run_files(account_id,run_id,file_id)" in migration
