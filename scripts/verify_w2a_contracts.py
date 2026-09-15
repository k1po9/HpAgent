"""W2-A checks with disposable PostgreSQL/Redis and an isolated Temporal namespace.

Usage: .venv/bin/python scripts/verify_w2a_contracts.py [pytest paths/options]
This validates the shared command boundary, not the full W2 QQ/G06 gate.
"""
import os
import subprocess
from uuid import uuid4

from verify_w1b_contracts import DEFAULT_TESTS, main

DEFAULT_TESTS[:] = [
    "test/web_persistence/test_conversation_commands.py",
    "test/web_persistence/test_command_service.py",
    "test/web_persistence/test_phase_a_invariants.py",
    "test/web_persistence/test_phase_a_hardening.py",
    "test/web_persistence/test_phase_c_sessions.py",
    "test/web_persistence/test_outbox_and_lifecycle.py",
    "test/web_api/test_command_projection.py",
    "test/web_api/test_phase_b_api.py",
    "test/web_api/test_sse_gateway.py",
    "test/web_persistence/test_web_temporal_e2e.py",
    "test/web_api/test_research_file_composition.py",
    "test/web_persistence/test_file_action_approvals.py",
    "test/web_persistence/test_run_budget.py",
    "test/web_persistence/test_memory_retention.py",
    "test/web_persistence/test_web_artifacts.py",
]


def verify():
    name = "hpagent-w2a-redis-" + uuid4().hex[:10]
    subprocess.run([
        "docker", "run", "--rm", "-d", "--name", name,
        "-p", "127.0.0.1::6379", "redis:7-alpine",
    ], check=True, stdout=subprocess.DEVNULL)
    previous = os.environ.get("REDIS_URL")
    try:
        address = subprocess.check_output(["docker", "port", name, "6379"], text=True).strip()
        os.environ["REDIS_URL"] = f"redis://127.0.0.1:{address.rsplit(':', 1)[1]}/0"
        return main()
    finally:
        if previous is None:
            os.environ.pop("REDIS_URL", None)
        else:
            os.environ["REDIS_URL"] = previous
        subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(verify())
