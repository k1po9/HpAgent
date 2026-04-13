import logging
from src.core.config import AppConfig
from src.context.session_store import SessionStore
from src.execution.llm_executor import ModelExecutor
from src.execution.agent_runner import run_reply_agent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConsoleChannel:
    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        model_executor: ModelExecutor,
    ):
        self.config = config
        self.session_store = session_store
        self.model_executor = model_executor
        self.session_key = "console_user_default"

    def start(self) -> None:
        """循环读取用户输入，调用 run_reply_agent，打印回复。输入 'exit' 退出。"""
        print("Welcome to HpAgent Console Channel!")
        print("Type 'exit' to quit.\n")

        while True:
            try:
                user_input = input("You: ").strip()
                if not user_input:
                    continue
                if user_input.lower() == "exit":
                    print("Goodbye!")
                    break

                payload = run_reply_agent(
                    user_message=user_input,
                    session_key=self.session_key,
                    config=self.config,
                    session_store=self.session_store,
                    model_executor=self.model_executor,
                )

                if payload.is_error:
                    print(f"Error: {payload.text}\n")
                else:
                    print(f"Assistant: {payload.text}\n")

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                logger.error(f"Error in console channel: {e}")
                print(f"An error occurred: {e}\n")
