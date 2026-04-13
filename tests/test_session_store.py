import pytest
import threading
import time
from src.context.session_store import SessionStore


class TestSessionStore:
    def test_get_history_empty(self):
        store = SessionStore()
        history = store.get_history("nonexistent_session")
        assert history == []

    def test_append_turn(self):
        store = SessionStore()
        store.append_turn("session1", "Hello", "Hi there!")
        history = store.get_history("session1")
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Hello"}
        assert history[1] == {"role": "assistant", "content": "Hi there!"}

    def test_append_multiple_turns(self):
        store = SessionStore()
        store.append_turn("session1", "First", "Response 1")
        store.append_turn("session1", "Second", "Response 2")
        history = store.get_history("session1")
        assert len(history) == 4
        assert history[0]["content"] == "First"
        assert history[2]["content"] == "Second"

    def test_get_history_returns_copy(self):
        store = SessionStore()
        store.append_turn("session1", "Hello", "Hi")
        history1 = store.get_history("session1")
        history2 = store.get_history("session1")
        assert history1 is not history2
        assert history1 == history2

    def test_clear(self):
        store = SessionStore()
        store.append_turn("session1", "Hello", "Hi")
        store.clear("session1")
        history = store.get_history("session1")
        assert history == []

    def test_clear_nonexistent(self):
        store = SessionStore()
        store.clear("nonexistent")
        assert store.get_history("nonexistent") == []

    def test_concurrent_access(self):
        store = SessionStore()
        errors = []

        def writer(session_id: str, count: int):
            try:
                for i in range(count):
                    store.append_turn(session_id, f"msg{i}", f"resp{i}")
            except Exception as e:
                errors.append(e)

        def reader(session_id: str):
            try:
                for _ in range(100):
                    store.get_history(session_id)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t1 = threading.Thread(target=writer, args=(f"session{i}", 20))
            t2 = threading.Thread(target=reader, args=(f"session{i}",))
            threads.extend([t1, t2])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_multiple_sessions(self):
        store = SessionStore()
        store.append_turn("session1", "msg1", "resp1")
        store.append_turn("session2", "msg2", "resp2")
        store.append_turn("session3", "msg3", "resp3")

        assert len(store.get_history("session1")) == 2
        assert len(store.get_history("session2")) == 2
        assert len(store.get_history("session3")) == 2
        assert store.get_history("session4") == []
