import yaml
import os
from pathlib import Path
from src.core.config import AppConfig, ModelConfig
from src.context.session_store import SessionStore
from src.execution.llm_executor import ModelExecutor
from src.channels.console_channel import ConsoleChannel


def load_config(config_path: str = "config.yaml") -> AppConfig:
    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    model_config = ModelConfig(
        provider=config_data.get("model", {}).get("provider", "openai"),
        model=config_data.get("model", {}).get("model", "gpt-4o-mini"),
        api_key=config_data.get("model", {}).get("api_key", ""),
        base_url=config_data.get("model", {}).get("base_url"),
        max_retries=config_data.get("model", {}).get("max_retries", 2),
        timeout_seconds=config_data.get("model", {}).get("timeout_seconds", 30),
    )

    app_config = AppConfig(
        model=model_config,
        max_history_turns=config_data.get("app", {}).get("max_history_turns", 10),
        system_prompt=config_data.get("app", {}).get(
            "system_prompt", "You are a helpful assistant."
        ),
    )

    return app_config


def main():
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please create a config.yaml file with your settings.")
        return

    if not config.model.api_key:
        print("Error: API key is not set in config.yaml")
        return

    session_store = SessionStore()
    model_executor = ModelExecutor(config.model)
    channel = ConsoleChannel(config, session_store, model_executor)

    try:
        channel.start()
    finally:
        model_executor.close()


if __name__ == "__main__":
    main()
