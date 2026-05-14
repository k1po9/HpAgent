"""
Memory 模块测试 —— HindsightClient 和 MemoryItem 的单元测试。

测试覆盖:
  1. MemoryItem 数据类
  2. HindsightClient 初始化与配置
  3. 禁用模式（enabled=False）降级行为
  4. 无服务时的降级行为（无 HTTP 调用）
  5. recall_formatted 格式化输出
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory.hindsight_client import HindsightClient, MemoryItem


class TestMemoryItem:
    """测试 MemoryItem 数据类。"""

    def test_from_dict_full(self):
        data = {
            "content": "用户偏好简洁的回答",
            "relevance": 0.95,
            "memory_type": "preference",
            "source_session_id": "sess_001",
            "created_at": "2026-05-09T10:00:00Z",
        }
        item = MemoryItem.from_dict(data)
        assert item.content == "用户偏好简洁的回答"
        assert item.relevance == 0.95
        assert item.memory_type == "preference"
        assert item.source_session_id == "sess_001"
        assert item.created_at == "2026-05-09T10:00:00Z"

    def test_from_dict_minimal(self):
        item = MemoryItem.from_dict({"content": "hello"})
        assert item.content == "hello"
        assert item.relevance == 1.0
        assert item.memory_type == ""
        assert item.source_session_id == ""

    def test_from_dict_alt_keys(self):
        """测试 Hindsight API 可能使用的替代字段名。"""
        data = {
            "content": "test",
            "type": "fact",
            "session_id": "s1",
        }
        item = MemoryItem.from_dict(data)
        assert item.memory_type == "fact"
        assert item.source_session_id == "s1"

    def test_default_values(self):
        item = MemoryItem(content="test")
        assert item.relevance == 1.0
        assert item.memory_type == ""
        assert item.source_session_id == ""


class TestHindsightClientConfig:
    """测试 HindsightClient 初始化与配置。"""

    def test_default_init(self):
        client = HindsightClient()
        assert client.base_url == "http://hindsight:8000"
        assert client.api_key == ""
        assert client.timeout == 30.0
        assert client.enabled is True

    def test_custom_init(self):
        client = HindsightClient(
            base_url="http://custom:9000",
            api_key="sk-test",
            timeout=10.0,
        )
        assert client.base_url == "http://custom:9000"
        assert client.api_key == "sk-test"
        assert client.timeout == 10.0

    def test_base_url_strips_trailing_slash(self):
        client = HindsightClient(base_url="http://hindsight:8000/")
        assert client.base_url == "http://hindsight:8000"

    def test_headers_without_api_key(self):
        client = HindsightClient()
        headers = client._headers()
        assert headers == {"Content-Type": "application/json"}

    def test_headers_with_api_key(self):
        client = HindsightClient(api_key="sk-test")
        headers = client._headers()
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer sk-test"


class TestHindsightClientDisabled:
    """测试 Hindsight 禁用时的降级行为（无 HTTP 调用）。"""

    def test_recall_disabled(self):
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.recall("query", "u1", "s1"))
        assert result == []

    def test_retain_disabled(self):
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.retain([], "u1", "s1"))
        assert result == 0

    def test_reflect_disabled(self):
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.reflect("u1"))
        assert result == 0

    def test_recall_formatted_disabled(self):
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.recall_formatted("query", "u1"))
        assert result == ""


class TestRecallFormatted:
    """测试记忆格式化输出。"""

    def test_empty_memories(self):
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.recall_formatted("q", "u1"))
        assert result == ""

    def test_formatted_structure(self):
        """验证格式化输出的结构（通过检查 disabled 模式）。"""
        client = HindsightClient(enabled=False)
        result = asyncio.run(client.recall_formatted("q", "u1", "s1", 5))
        assert isinstance(result, str)


class TestHindsightClientNoServer:
    """测试 Hindsight 服务不可用时的降级（无真实服务）。"""

    def test_recall_no_server(self):
        """向不存在的服务发起 recall，应返回空列表。"""
        client = HindsightClient(
            base_url="http://127.0.0.1:19999",  # 不存在的端口
            timeout=1.0,
        )
        result = asyncio.run(client.recall("query", "u1", "s1"))
        assert result == []

    def test_retain_no_server(self):
        client = HindsightClient(
            base_url="http://127.0.0.1:19999",
            timeout=1.0,
        )
        result = asyncio.run(client.retain([], "u1", "s1"))
        assert result == 0

    def test_reflect_no_server(self):
        client = HindsightClient(
            base_url="http://127.0.0.1:19999",
            timeout=1.0,
        )
        result = asyncio.run(client.reflect("u1"))
        assert result == 0

    def test_recall_formatted_no_server(self):
        client = HindsightClient(
            base_url="http://127.0.0.1:19999",
            timeout=1.0,
        )
        result = asyncio.run(client.recall_formatted("q", "u1"))
        assert result == ""


class TestMemoryItemSorting:
    """测试记忆按相关性排序。"""

    def test_sort_by_relevance(self):
        items = [
            MemoryItem(content="a", relevance=0.3),
            MemoryItem(content="b", relevance=0.9),
            MemoryItem(content="c", relevance=0.6),
        ]
        items.sort(key=lambda m: m.relevance, reverse=True)
        assert items[0].content == "b"
        assert items[1].content == "c"
        assert items[2].content == "a"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
