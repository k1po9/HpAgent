"""W3-B canonical ingress, registry and recovery regression in isolated services."""

from verify_w2_contracts import DEFAULT_TESTS, verify

DEFAULT_TESTS.extend([
    "test/test_web_temporal_integration.py",
    "test/test_document_temporal_integration.py",
    "test/test_research_temporal_integration.py",
])

if __name__ == "__main__":
    raise SystemExit(verify())
