"""
TokenCounter — 基于字符的 token 数量估算。

不依赖 tiktoken，使用保守估算策略：
  - CJK 字符：每字符约 1 token
  - ASCII 字符：每 3 字符约 1 token
  - 其他 Unicode：每 2 字符约 1 token

budget 计算额外预留 20% 安全边际应对估算误差。
"""
from __future__ import annotations

import re

_CJK_RE = re.compile(
    r'[一-鿿㐀-䶿豈-﫿'
    r'　-〿぀-ゟ゠-ヿ가-힯]'
)


def estimate_tokens(text: str) -> int:
    """基于字符分类估算 token 数。

    保守估算（上取整）:
      - CJK 字符: 每字符 1 token
      - ASCII 字符: 每 3 字符 1 token
      - 其他（emoji 等）: 每 2 字符 1 token
    """
    if not text:
        return 0

    cjk = 0
    ascii_chars = 0
    other = 0
    for c in text:
        if _CJK_RE.match(c):
            cjk += 1
        elif c.isascii():
            ascii_chars += 1
        else:
            other += 1

    estimated = cjk
    if ascii_chars:
        estimated += (ascii_chars + 2) // 3       # 上取整
    if other:
        estimated += (other + 1) // 2             # 上取整
    return max(estimated, 1)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算一组 LLM messages 的总 token 数。

    对每条消息的 content 进行估算，外加约 4 token 的消息级开销。
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total += estimate_tokens(block.get("text", ""))
        total += 4  # role + formatting overhead
    return total


def budget_safe(total: int, *, margin: float = 0.20) -> int:
    """预留安全边际后的可用预算。

    Args:
        total:  原始预算 (token 数)。
        margin: 安全边际比例，默认 20%。

    Returns:
        缩减后的安全预算。
    """
    return int(total * (1.0 - margin))
