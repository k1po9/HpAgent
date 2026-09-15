"""W2-C delivery and shared W2 ingress/runtime regression with isolated services."""

from verify_w2b_contracts import DEFAULT_TESTS, verify

DEFAULT_TESTS.extend(
    [
        "test/web_persistence/test_qq_delivery.py",
        "test/test_qq_delivery_adapter.py",
    ]
)

if __name__ == "__main__":
    raise SystemExit(verify())
