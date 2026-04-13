from typing import Dict, List
from threading import RLock


class SessionStore:
    def __init__(self):
        self._storage: Dict[str, List[Dict[str, str]]] = {}
        self._lock = RLock()

    def get_history(self, session_key: str) -> List[Dict[str, str]]:
        """返回会话历史列表副本，若无则返回空列表"""
        with self._lock:
            return list(self._storage.get(session_key, []))

    def append_turn(self, session_key: str, user_msg: str, assistant_msg: str) -> None:
        """追加一轮对话（user + assistant）"""
        with self._lock:
            if session_key not in self._storage:
                self._storage[session_key] = []
            self._storage[session_key].append({"role": "user", "content": user_msg})
            self._storage[session_key].append({"role": "assistant", "content": assistant_msg})

    def clear(self, session_key: str) -> None:
        """清除指定会话历史"""
        with self._lock:
            if session_key in self._storage:
                del self._storage[session_key]
