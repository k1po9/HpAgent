import asyncio
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from src.session import SessionManager
from src.resources import ResourcePool, CredentialManager, ModelEndpoint
from src.sandbox import SandboxManager
from src.sandbox.channels import ConsoleChannel
from src.sandbox.tools.factory import ToolFactory
from src.harness import Harness, HarnessContextBuilder
from src.orchestration import Orchestrator
from src.common.types import ChannelType, UnifiedMessage
from src.common.errors import AgentError


@dataclass
class AppConfig:
    api_key: str = "sk-cp-5JEOvwVXJ2ZQKmTaZ58k4YCzcQec5gWAqpZvl8xAl2ALaHO9RMthWAo7Yg2hfmj9KEj-LUDIO3WSZSU2J4d0nxRMbv37d602rdjehWOj0Dyk8QjKl_030tM"
    base_url: str = "https://api.minimaxi.com/anthropic"
    model: str = "MiniMax-M2.7"
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
        base_url=model_config.get("base_url", ""),
        model=model_config.get("model", "MiniMax-M2.7"),
        max_history_turns=app_config.get("max_history_turns", 10),
        system_prompt=app_config.get("system_prompt", "You are a helpful AI assistant."),
        max_turns=app_config.get("max_turns", 20),
    )


class AgentApplication:
    def __init__(self, config: AppConfig):
        self.config = config
        self.session_manager = SessionManager()
        self.credential_manager = CredentialManager()
        self.resource_pool = ResourcePool(self.credential_manager)
        self.sandbox_manager = SandboxManager()
        self.harness: Optional[Harness] = None
        self.orchestrator: Optional[Orchestrator] = None
        self.console_channel: Optional[ConsoleChannel] = None
        self._initialized = False

    async def initialize_async(self):
        if self._initialized:
            return
        if not self.config.api_key:
            raise ValueError("API key is not set in config.yaml")

        self.credential_manager.register_model_chain(
            [
                ModelEndpoint(
                    provider="anthropic",
                    api_key=self.config.api_key,
                    base_url=self.config.base_url,
                    model=self.config.model,
                ),
            ]
        )
        await self.resource_pool.initialize_models()

        self.harness = Harness(
            session_store=self.session_manager,
            resource_pool=self.resource_pool,
            sandbox_manager=self.sandbox_manager,
            system_prompt=self.config.system_prompt,
            max_turns=self.config.max_turns,
        )

        tools = ToolFactory.create_default_tools()
        self.orchestrator = Orchestrator(
            session_manager=self.session_manager,
            harness=self.harness,
            sandbox_manager=self.sandbox_manager,
            resource_pool=self.resource_pool,
        )
        self.console_channel = ConsoleChannel()
        self.orchestrator.provision_sandbox([t.name for t in tools], {})
        self._initialized = True

    async def handle_message(self, message: UnifiedMessage):
        if not self._initialized:
            raise AgentError("Application not initialized. Call initialize() first.")
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
        await app.initialize_async()
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
