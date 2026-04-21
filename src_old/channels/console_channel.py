import logging
from src.core.config import AppConfig
from src.context.session_store import SessionStore
from src.model.client import ModelClient
from src.execution.agent_runner import run_reply_agent
from src.tools.service import ToolService
from src.execution.harness.events import EventType, ExecutionEvent


class ThinkStreamTracker:
    """
    跟踪流式输出中的  块状态，区分思考内容和正式回答
    """
    def __init__(self):
        # 状态变量：当前是否处于思考块内
        self.is_in_think: bool = False

        # 前缀打印状态：确保每个块只打印一次前缀
        self.has_printed_think_start: bool = False
        self.has_printed_answer_start: bool = False

        # ANSI 颜色码（控制台输出用）
        self.COLOR_THINK = "\033[90m"  # 灰色
        self.COLOR_ANSWER = "\033[0m"  # 默认颜色
        self.PREFIX_THINK = "[思考] "
        self.PREFIX_ANSWER = "[回答] "

    def process_text_delta(self, text: str) -> None:
        """
        处理单段文本增量，实时检测标签并输出
        """
        remaining_text = text

        while remaining_text:
            if not self.is_in_think:
                # ------------------- 当前不在思考块：找  -------------------
                think_start = remaining_text.find("<think>")
                if think_start == -1:
                    # 没有 ，全部是正式回答
                    self._print_answer(remaining_text)
                    remaining_text = ""
                else:
                    # 找到 ：先输出前面的正式回答，再进入思考模式
                    answer_part = remaining_text[:think_start]
                    if answer_part:
                        self._print_answer(answer_part)
                    # 跳过思考标签
                    remaining_text = remaining_text[think_start + len("<think>"):]
                    self.is_in_think = True
                    # 第一次进入思考块
                    if not self.has_printed_think_start:
                        self.has_printed_think_start = True
                        print(f"\n{self.COLOR_THINK}{self.PREFIX_THINK}────── 思考开始 ──────{self.COLOR_ANSWER}")
            else:
                # ------------------- 当前在思考块：找  -------------------
                think_end = remaining_text.find("</think>")
                if think_end == -1:
                    # 全部是思考内容                    
                    self._print_think(remaining_text)
                    remaining_text = ""
                else:
                    # 找到 ：先输出前面的思考内容，再退出思考模式
                    think_part = remaining_text[:think_end]
                    if think_part:
                        self._print_think(think_part)
                    # 跳过思考标签
                    remaining_text = remaining_text[think_end + len("</think>"):]
                    self.is_in_think = False
                    # 第一次退出思考块
                    if not self.has_printed_think_start:
                        self.has_printed_think_start = True
                        print(f"\n{self.COLOR_THINK}{self.PREFIX_THINK}────── 思考结束 ──────{self.COLOR_ANSWER}\n")

    def _print_think(self, text: str) -> None:
        """输出思考内容（灰色+前缀，前缀只打印一次）"""
        if self.has_printed_think_start and not self._has_printed_think_content:
            self._has_printed_think_content = True
        
        # 直接打印文本内容
        print(f"{self.COLOR_THINK}{text}{self.COLOR_ANSWER}", end="", flush=True)

    def _print_answer(self, text: str) -> None:
        """输出正式回答（默认颜色+前缀，前缀只打印一次）"""
        if not self.has_printed_answer_start:
            print(f"{self.COLOR_ANSWER}{self.PREFIX_ANSWER}", end="", flush=True)
            self.has_printed_answer_start = True
        
        # 直接打印文本内容
        print(f"{self.COLOR_ANSWER}{text}", end="", flush=True)

    def reset(self) -> None:
        """重置状态机（用于多轮对话）"""
        self.__init__()

    @property
    def _has_printed_think_content(self) -> bool:
        if not hasattr(self, '_has_printed_think_content_internal'):
            self._has_printed_think_content_internal = False
        return self._has_printed_think_content_internal

    @_has_printed_think_content.setter
    def _has_printed_think_content(self, value: bool) -> None:
        self._has_printed_think_content_internal = value


class ConsoleChannel:
    def __init__(
        self,
        config: AppConfig,
        session_store: SessionStore,
        tool_service: ToolService,
    ):
        self.config = config
        self.session_store = session_store
        self.session_key = "console_user_default"
        self.tool_service = tool_service
        self.think_tracker = ThinkStreamTracker()

    async def _event_handler(self, event: ExecutionEvent):
        if event.type == EventType.TEXT_DELTA:
            # 从 event.data 中获取文本增量（根据你的实际数据结构调整 key）
            text_delta = event.data.get("content", "")
            if text_delta:
                self.think_tracker.process_text_delta(text_delta)
        elif event.type == EventType.TURN_COMPLETED:
            # 可选：回合结束时重置状态（如果是多轮对话）
            self.think_tracker.reset()

    def start(self) -> None:
        """循环读取用户输入，调用 run_reply_agent，打印回复。输入 'exit' 退出。"""
        print("\nWelcome to HpAgent Console Channel!")
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
                    config=self.config,
                    user_message=user_input,                 
                    session_store=self.session_store,
                    tool_service=self.tool_service,
                    on_event=self._event_handler
                )

                if payload.is_error:
                    print(f"Error: {payload.text}\n")
                else:
                    # print(f"Assistant: {payload.text}\n")
                    print("\n")

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                logger.error(f"Error in console channel: {e}")
                print(f"An error occurred: {e}\n")
