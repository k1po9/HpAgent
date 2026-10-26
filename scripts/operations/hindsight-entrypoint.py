#!/usr/bin/env python3
"""Load the shared quality file, then exec the existing Hindsight launcher."""
import os
import sys
from pathlib import Path

# The service mounts this pure configuration module beside the entry point.
sys.path.insert(0, str(Path(__file__).parent))
from quality_config import MemoryQualityConfig  # noqa: E402


def main():
    try:
        config = MemoryQualityConfig.from_yaml(
            os.environ.get("HPAGENT_HINDSIGHT_CONFIG", "/etc/hpagent/hindsight.yaml"))
        if config.service is None:
            raise ValueError('service configuration is required by the Hindsight launcher')
        environment = {**os.environ, **config.service_environment(os.environ)}
    except Exception:
        print("Invalid Hindsight quality/service configuration; values hidden.", file=sys.stderr)
        return 2
    os.execvpe("bash", ["bash", "/app/start-all.sh"], environment)


if __name__ == "__main__":
    raise SystemExit(main())
