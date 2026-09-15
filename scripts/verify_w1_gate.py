"""W1 G02/G03/G04 validation in isolated PostgreSQL and Temporal fixtures."""
from verify_w1c_contracts import DEFAULT_TESTS, main

DEFAULT_TESTS.extend([
    "test/test_w1_gate_resources.py",
    "test/web_persistence/test_w1_source_data_plane.py",
    "test/test_workspace_isolation.py",
    "test/test_workspace_provisioning.py",
    "test/test_web_background_tasks.py",
])

if __name__ == "__main__":
    raise SystemExit(main())
