"""W2-B ingress and canonical runtime checks; full QQ delivery Gate remains pending."""
from verify_w2a_contracts import DEFAULT_TESTS, verify

DEFAULT_TESTS[:] = [
    "test/web_persistence/test_qq_canonical_ingress.py",
    "test/web_persistence/test_web_temporal_e2e.py",
    "test/web_persistence/test_phase_c_context.py",
    "test/web_persistence/test_memory_retention.py",
    "test/web_persistence/test_conversation_commands.py",
    "test/web_persistence/test_phase_c_sessions.py",
    "test/test_workspace_isolation.py",
    "test/test_workspace_provisioning.py",
    "test/test_trace_events.py",
    "test/test_qq_canonical_protocol.py",
    "test/test_unbound_identity.py",
    "test/test_identity_command_interception.py",
    "test/test_w1_gate_resources.py",
    "test/test_web_temporal_contract.py",
]

if __name__ == "__main__":
    raise SystemExit(verify())
