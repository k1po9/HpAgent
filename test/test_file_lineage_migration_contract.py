from pathlib import Path


def test_file_lineage_migration_is_scope_safe_and_database_versioned():
    migration = Path("persistence/migrations/027_file_output_lineage.sql").read_text()
    assert "FOREIGN KEY(account_id,conversation_id,parent_file_id)" in migration
    assert "NEW.version := parent.version + 1" in migration
    assert "only output files may have a parent" in migration
    assert "parent.status <> 'ready'" in migration
    assert "WHERE parent_file_id IS NOT NULL" in migration


def test_ready_lineage_is_frozen_by_followup_migration():
    migration = Path("persistence/migrations/028_file_lineage_immutability.sql").read_text()
    assert "OLD.status='ready'" in migration
    assert "NEW.parent_file_id IS DISTINCT FROM OLD.parent_file_id" in migration
    assert "ready file lineage is immutable" in migration
