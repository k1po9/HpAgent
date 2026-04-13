import pytest
from unittest.mock import Mock, MagicMock
from src.core.config import AppConfig, ModelConfig
from src.core.types import TemplateContext, ReplyPayload
from src.context.session_store import SessionStore
from src.execution.llm_executor import ModelExecutor, ModelError
from src.execution.agent_runner import run_reply_agent


class TestAgentRunner:
    def test_successful_reply_flow(self):
        config = AppConfig(
            system_prompt="You are helpful.",
            max_history_turns=10,
        )
        store = SessionStore()
        executor = Mock(spec=ModelExecutor)
        executor.generate.return_value = "This is a test response."

        payload = run_reply_agent(
            user_message="Hello",
            session_key="test_session",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        assert payload.is_error is False
        assert payload.text == "This is a test response."

        history = store.get_history("test_session")
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Hello"}
        assert history[1] == {"role": "assistant", "content": "This is a test response."}

        executor.generate.assert_called_once()
        call_args = executor.generate.call_args[0][0]
        assert isinstance(call_args, TemplateContext)
        assert call_args.body == "Hello"

    def test_error_handling_does_not_save_history(self):
        config = AppConfig(system_prompt="You are helpful.")
        store = SessionStore()
        store.append_turn("test_session", "old_user", "old_assistant")

        executor = Mock(spec=ModelExecutor)
        executor.generate.side_effect = ModelError("API Error", status_code=500)

        payload = run_reply_agent(
            user_message="Hello",
            session_key="test_session",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        assert payload.is_error is True
        assert "API Error" in payload.text

        history = store.get_history("test_session")
        assert len(history) == 2
        assert history[0]["content"] == "old_user"

    def test_unexpected_error_handling(self):
        config = AppConfig(system_prompt="You are helpful.")
        store = SessionStore()

        executor = Mock(spec=ModelExecutor)
        executor.generate.side_effect = RuntimeError("Unexpected error")

        payload = run_reply_agent(
            user_message="Hello",
            session_key="test_session",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        assert payload.is_error is True
        assert "unexpected error" in payload.text.lower()
        assert store.get_history("test_session") == []

    def test_conversation_context_preserved(self):
        config = AppConfig(
            system_prompt="You are a chatbot.",
            max_history_turns=10,
        )
        store = SessionStore()
        executor = Mock(spec=ModelExecutor)
        executor.generate.side_effect = [
            "First response",
            "Second response",
        ]

        run_reply_agent(
            user_message="First message",
            session_key="session1",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        run_reply_agent(
            user_message="Second message",
            session_key="session1",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        assert executor.generate.call_count == 2

        context_second_call = executor.generate.call_args_list[1][0][0]
        assert len(context_second_call.conversation_history) == 4

        expected_history = [
            {"role": "system", "content": "You are a chatbot."},
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
        ]
        assert context_second_call.conversation_history == expected_history

    def test_history_truncation_applied(self):
        config = AppConfig(
            system_prompt="You are helpful.",
            max_history_turns=2,
        )
        store = SessionStore()

        for i in range(5):
            store.append_turn("session1", f"user{i}", f"assistant{i}")

        executor = Mock(spec=ModelExecutor)
        executor.generate.return_value = "New response"

        run_reply_agent(
            user_message="New message",
            session_key="session1",
            config=config,
            session_store=store,
            model_executor=executor,
        )

        context = executor.generate.call_args[0][0]
        expected_len = 1 + 4 + 1
        assert len(context.conversation_history) == expected_len
