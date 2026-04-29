"""
HpAgent — main entrypoint.

Boot sequence:
  1. Load config.yaml
  2. Delegate to Orchestration Worker (Temporal mode)
     → src/orchestration/worker.py
"""
import asyncio
import logging
import sys
import yaml
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("HpAgent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from orchestration.worker import start_worker


@dataclass
class AppConfig:
    api_key: str
    base_url: str
    model: str
    max_history_turns: int
    max_turns: int
    temporal_host: str = "localhost:7233"


def load_config(config_path: str = "config.yaml") -> AppConfig:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    model_config = config_data.get("model", {})
    app_config = config_data.get("app", {})
    return AppConfig(
        api_key=model_config["api_key"],
        base_url=model_config["base_url"],
        model=model_config["model"],
        max_history_turns=app_config.get("max_history_turns", 20),
        max_turns=app_config.get("max_turns", 20),
        temporal_host=app_config.get("temporal_host", "localhost:7233"),
    )


async def main_async():
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please create a config.yaml file with your settings.")
        return

    worker_config = {
        "api_key": config.api_key,
        "base_url": config.base_url,
        "model": config.model,
        "temporal_host": config.temporal_host,
    }

    print("\n=== HpAgent ===")
    print(f"Temporal Server: {config.temporal_host}")
    print("Task Queue: hpagent-task-queue")
    print("Starting Orchestration Worker + NapCat channel...\n")
    await start_worker(worker_config)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
