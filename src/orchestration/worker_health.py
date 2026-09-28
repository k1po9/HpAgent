"""Compose health probe for the Web worker startup contract."""
import json
import os
import time
from pathlib import Path


def main() -> int:
    path = Path(os.getenv("HPAGENT_WORKER_READY_FILE", "/tmp/hpagent-worker-ready.json"))
    try:
        state = json.loads(path.read_text())
        required = (
            "schema_verified", "temporal_connected", "lifecycle_worker_started",
            "agent_worker_started", "dispatcher_started",
        )
        if not all(state.get(key) is True for key in required):
            return 1
        os.kill(state["pid"], 0)
        return 0 if time.time() - state["updated_at"] < 15 else 1
    except (OSError, ValueError, KeyError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
