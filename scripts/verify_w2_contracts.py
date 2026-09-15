"""W2/G06 cross-surface and recovery acceptance with isolated PostgreSQL/Temporal."""
from verify_w2c_contracts import DEFAULT_TESTS, verify

DEFAULT_TESTS.extend([
    'test/web_persistence/test_w2_cross_surface.py',
    'test/test_web_outbox_recovery.py',
    'test/web_persistence/test_outbox_and_lifecycle.py',
    'test/test_durable_agent_worker_kill.py',
    'test/web_persistence/test_durable_agent_activity_worker_kill.py',
    'test/web_api/test_phase_b_api.py',
    'test/web_api/test_sse_gateway.py',
])

if __name__ == '__main__':
    raise SystemExit(verify())
