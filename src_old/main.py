import yaml
import os
from pathlib import Path
from src.core.config import AppConfig, ModelConfig, LoopConfig, ToolConfig
from src.tools import ToolService
from src.context.session_store import SessionStore
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

    tool_config = ToolConfig(
        enable_native=True,
        enable_mcp=False,
        enable_skills=True,
        validate_before_execute=True,
    )

    loop_config = LoopConfig(
        max_turns=config_data.get("app", {}).get("max_turns", 20),
    )

    app_config = AppConfig(
        model=model_config,
        loop=loop_config,
        tool=tool_config,
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
    tool_service = ToolService(config.tool)
    channel = ConsoleChannel(config, session_store, tool_service)

    try:
        channel.start()
    finally:
        pass


if __name__ == "__main__":
    main()
