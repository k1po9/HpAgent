"""W1-C checks using the disposable PostgreSQL/Temporal fixture runner."""
from verify_w1b_contracts import DEFAULT_TESTS, main

DEFAULT_TESTS.extend([
    "test/web_persistence/test_web_temporal_e2e.py",
    "test/web_api/test_config.py",
    "test/web_persistence/test_file_action_approvals.py",
    "test/test_w1c_cutover_contract.py",
    "test/test_web_temporal_integration.py",
])

if __name__ == "__main__":
    raise SystemExit(main())
