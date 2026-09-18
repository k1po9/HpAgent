from pathlib import Path


def test_account_daily_budget_migration_freezes_utc_ledger_contract() -> None:
    sql = Path("persistence/migrations/038_account_daily_model_budget.sql").read_text()
    assert "PRIMARY KEY(account_id,quota_date)" in sql
    assert "PRIMARY KEY(account_id,quota_date,operation_id)" in sql
    assert "state IN ('reserved','settled','released')" in sql
    assert "usage_source IS NULL OR usage_source IN ('provider','measured','estimated')" in sql
    assert "GRANT SELECT,INSERT,UPDATE" in sql
    assert "TO hpagent_worker" in sql
