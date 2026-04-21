import asyncio
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from src.session import EventStore, SessionManager
from src.resources import ResourcePool, CredentialManager
from src.sandbox import SandboxManager
from src.sandbox.channels import ConsoleChannel
from src.sandbox.tools.factory import ToolFactory
from src.harness import Harness, HarnessContextBuilder
from src.orchestration import Orchestrator
from src.common.types import ChannelType, UnifiedMessage
from src.common.errors import AgentError


@dataclass
class AppConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    max_history_turns: int = 10
    system_prompt: str = "You are a helpful AI assistant."
    max_turns: int = 20


def load_config(config_path: str = "config.yaml") -> AppConfig:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_file, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
    model_config = config_data.get("model", {})
    app_config = config_data.get("app", {})
    return AppConfig(
        api_key=model_config.get("api_key", ""),
        base_url=model_config.get("base_url", "https://api.openai.com/v1"),
        model=model_config.get("model", "gpt-4o-mini"),
        max_history_turns=app_config.get("max_history_turns", 10),
        system_prompt=app_config.get("system_prompt", "You are a helpful AI assistant."),
        max_turns=app_config.get("max_turns", 20),
    )


class AgentApplication:
    def __init__(self, config: AppConfig):
        self.config = config
        self.event_store = EventStore()
        self.session_manager = SessionManager(self.event_store)
        self.credential_manager = CredentialManager()
        self.resource_pool = ResourcePool(self.credential_manager)
        self.sandbox_manager = SandboxManager()
        self.harness = Harness(
            session_store=self.event_store,
            resource_pool=self.resource_pool,
            sandbox_manager=self.sandbox_manager,
            system_prompt=config.system_prompt,
            max_turns=config.max_turns,
        )
        self.orchestrator = Orchestrator(
            session_manager=self.session_manager,
            harness=self.harness,
            sandbox_manager=self.sandbox_manager,
            resource_pool=self.resource_pool,
        )
        self.console_channel = ConsoleChannel()
        self._initialized = False

    def initialize(self):
        if self._initialized:
            return
        if not self.config.api_key:
            raise ValueError("API key is not set in config.yaml")
        self.credential_manager.register_credential(
            resource_id="model_api",
            credential_type="api_key",
            value=self.config.api_key,
            scope=["model:read", "model:write"],
        )
        tools = ToolFactory.create_default_tools()
        self.orchestrator.provision_sandbox([t.name for t in tools], {})
        self._initialized = True

    async def handle_message(self, message: UnifiedMessage):
        try:
            result = await self.orchestrator.receive_request(message)
            session_id = result["session_id"]
            process_result = await self.orchestrator.process_session(session_id)
            response_message = UnifiedMessage(
                session_id=session_id,
                sender_id="assistant",
                channel_type=message.channel_type,
                content=process_result.get("content", ""),
            )
            await self.console_channel.send_message(response_message)
        except AgentError as e:
            print(f"Agent error: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")


async def main_async():
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please create a config.yaml file with your settings.")
        return
    app = AgentApplication(config)
    try:
        app.initialize()
    except ValueError as e:
        print(f"Error: {e}")
        return
    print("\n=== HpAgent Console (New Architecture) ===")
    print("Type 'exit' to quit.\n")
    await app.console_channel.start_interactive(app.handle_message)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
