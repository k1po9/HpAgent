import pytest
from src.context.session_store import SessionStore
from src.context.context_builder import build_context


class TestContextBuilder:
    def test_build_context_empty_history(self):
        store = SessionStore()
        context = build_context(
            user_message="Hello",
            session_key="session1",
            session_store=store,
            system_prompt="You are a helpful assistant.",
            max_history_turns=10,
        )

        assert context.body == "Hello"
        assert context.session_key == "session1"
        assert len(context.conversation_history) == 2
        assert context.conversation_history[0] == {
            "role": "system",
            "content": "You are a helpful assistant."
        }
        assert context.conversation_history[1] == {
            "role": "user",
            "content": "Hello"
        }

    def test_build_context_with_history(self):
        store = SessionStore()
        store.append_turn("session1", "Previous message", "Previous response")

        context = build_context(
            user_message="New message",
            session_key="session1",
            session_store=store,
            system_prompt="System prompt",
            max_history_turns=10,
        )

        assert len(context.conversation_history) == 4
        assert context.conversation_history[0]["role"] == "system"
        assert context.conversation_history[1]["content"] == "Previous message"
        assert context.conversation_history[2]["content"] == "Previous response"
        assert context.conversation_history[3]["content"] == "New message"

    def test_build_context_history_truncation(self):
        store = SessionStore()

        for i in range(25):
            store.append_turn("session1", f"user{i}", f"assistant{i}")

        context = build_context(
            user_message="Latest message",
            session_key="session1",
            session_store=store,
            system_prompt="System",
            max_history_turns=5,
        )

        expected_history_length = 1 + (5 * 2) + 1
        assert len(context.conversation_history) == expected_history_length
        assert context.conversation_history[0]["role"] == "system"

        last_messages = context.conversation_history[-3:]
        assert last_messages[0]["content"] == "user24"
        assert last_messages[1]["content"] == "assistant24"
        assert last_messages[2]["content"] == "Latest message"

    def test_build_context_system_prompt_first(self):
        store = SessionStore()
        store.append_turn("session1", "User msg", "Assistant msg")

        context = build_context(
            user_message="New",
            session_key="session1",
            session_store=store,
            system_prompt="Custom system prompt",
            max_history_turns=10,
        )

        assert context.conversation_history[0]["role"] == "system"
        assert context.conversation_history[0]["content"] == "Custom system prompt"

    def test_build_context_multiple_sessions_isolated(self):
        store = SessionStore()
        store.append_turn("session1", "msg1", "resp1")
        store.append_turn("session2", "msg2", "resp2")

        context1 = build_context(
            user_message="New1",
            session_key="session1",
            session_store=store,
            system_prompt="System",
            max_history_turns=10,
        )
        context2 = build_context(
            user_message="New2",
            session_key="session2",
            session_store=store,
            system_prompt="System",
            max_history_turns=10,
        )

        assert len(context1.conversation_history) == 4
        assert len(context2.conversation_history) == 4
        assert context1.conversation_history[1]["content"] == "msg1"
        assert context2.conversation_history[1]["content"] == "msg2"
        assert context1.conversation_history[3]["content"] == "New1"
        assert context2.conversation_history[3]["content"] == "New2"
