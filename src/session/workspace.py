"""
Session Workspace —— 会话工作目录与归档持久化。

路径约定:
  <user_uuid>/
    ├── repo/                             # git 工作区（多 session 共享）
    ├── skills/                           # 技能沉淀
    ├── sessions/<session_id>/
    │   ├── meta.yaml                     # 结构化元数据（含 fast 模型摘要）
    │   └── history.jsonl                 # 归档事件快照（完整 Event 列表）
    └── user_profile.yaml                 # 用户画像

归档时序（防丢数据）:
  1. 写 history.jsonl（先落盘）
  2. 生成 meta 摘要（fast 模型）→ 写入 meta.yaml
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import WorkspaceDB
from .models import Session, SessionStatus

logger = logging.getLogger("HpAgent.SessionWorkspace")

DIR_SKILLS = "skills"
DIR_SESSIONS = "sessions"
DIR_REPO = "repo"
FILE_SESSION_META = "meta.yaml"
FILE_SESSION_HISTORY = "history.jsonl"
FILE_USER_PROFILE = "user_profile.yaml"


def init_user(file_store, db: WorkspaceDB, user_uuid: str, username: str = "") -> None:
    """确保用户工作目录存在（幂等）。"""
    for subdir in [DIR_SKILLS, DIR_SESSIONS, DIR_REPO]:
        file_store.mkdir_sync(f"{user_uuid}/{subdir}")

    profile_rel = f"{user_uuid}/{FILE_USER_PROFILE}"
    if not file_store.exists_sync(profile_rel):
        _write_yaml(file_store, profile_rel, {
            "user_uuid": user_uuid,
            "username": username,
            "preferences": {},
            "created_at": _now_iso(),
        })

    db.upsert_user(
        user_uuid=user_uuid,
        username=username,
        profile_path=profile_rel,
        persistent_dir=f"{user_uuid}/{DIR_REPO}",
    )


def init_session(
    file_store,
    db: WorkspaceDB,
    user_uuid: str,
    session_id: Optional[str] = None,
    *,
    task_summary: str = "",
    tags: Optional[list[str]] = None,
) -> Session:
    """创建新会话并初始化目录 + meta.yaml（幂等——已存在则返回现有会话）。"""
    if session_id is None:
        session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    existing = db.get_session(session_id)
    if existing is not None:
        return existing

    session_rel = f"{user_uuid}/{DIR_SESSIONS}/{session_id}"
    file_store.mkdir_sync(session_rel)

    now = _now_iso()
    _write_yaml(file_store, f"{session_rel}/{FILE_SESSION_META}", {
        "session_id": session_id,
        "user_uuid": user_uuid,
        "status": SessionStatus.ACTIVE.value,
        "branch": f"hpagent/{session_id}",
        "task_summary": task_summary,
        "tags": tags or [],
        "event_count": 0,
        "tool_calls": 0,
        "created_at": now,
        "completed_at": "",
    })

    session = Session(
        session_id=session_id,
        account_id=user_uuid,
        status=SessionStatus.ACTIVE,
        task_summary=task_summary,
        session_dir=session_rel,
        output_dir=f"{user_uuid}/{DIR_REPO}",
        tags=tags or [],
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.insert_session(session)
    logger.info("Session initialized: %s for user %s", session_id, user_uuid)
    return session


def update_session_meta(file_store, user_uuid: str, session_id: str, **fields) -> None:
    """更新 meta.yaml 中的字段（合并写入）。"""
    import yaml

    session_rel = f"{user_uuid}/{DIR_SESSIONS}/{session_id}"
    meta_path = f"{session_rel}/{FILE_SESSION_META}"

    existing: dict = {}
    if file_store.exists_sync(meta_path):
        try:
            existing = yaml.safe_load(file_store.read_sync(meta_path)) or {}
        except Exception:
            pass

    existing.update(fields)
    _write_yaml(file_store, meta_path, existing)


# ── 归档持久化 ─────────────────────────────────────────────────────────────

def write_history_jsonl(
    file_store, user_uuid: str, session_id: str, events: list[dict],
) -> str:
    """将事件列表写入 history.jsonl —— 归档快照。

    每行一个 JSON 对象（Event.to_dict()），流式追加友好。
    返回写入的完整路径。
    """
    session_rel = f"{user_uuid}/{DIR_SESSIONS}/{session_id}"
    history_path = f"{session_rel}/{FILE_SESSION_HISTORY}"

    lines = []
    for e in events:
        if isinstance(e, dict):
            d = e
        elif hasattr(e, "to_dict"):
            d = e.to_dict()
        else:
            d = {"content": str(e)}
        lines.append(json.dumps(d, ensure_ascii=False) + "\n")

    content = "".join(lines)
    file_store.write_sync(history_path, content)
    logger.info("history.jsonl written: %s (%d events, %.1f KB)",
                session_id, len(events), len(content) / 1024)
    return history_path


# ── 标签校验正则 ──────────────────────────────────────────────────────────
# 合法标签: 1-8 个汉字/英文/数字/空格，不含 HTML/CSS/代码语法字符
_TAG_VALID_RE = re.compile(r"^[\u4e00-\u9fff\w][\u4e00-\u9fff\w ]{0,9}$")
# 非法模式: 含 HTML 标签、CSS 属性、代码片段、URL
_TAG_BLACKLIST_RE = re.compile(r"[<>{}();#.%/\\=@]|javascript:|data:|url\(|function\s*\(", re.IGNORECASE)


def _sanitize_tag(tag: str) -> str | None:
    """校验并清理单个标签，非法返回 None。"""
    tag = tag.strip(" '\"`，,、;:：；")
    if not tag:
        return None
    if len(tag) > 12:
        return None
    if _TAG_BLACKLIST_RE.search(tag):
        return None
    if not _TAG_VALID_RE.match(tag):
        return None
    return tag


def _sanitize_tags(tags: list[str]) -> list[str]:
    """批量校验标签，去重后返回。检测到大量非法标签则全部丢弃。"""
    cleaned = []
    for t in tags:
        cleaned_tag = _sanitize_tag(t)
        if cleaned_tag and cleaned_tag not in cleaned:
            cleaned.append(cleaned_tag)
    # 如果过半被过滤掉，说明模型输出质量差，全部丢弃
    if not tags:
        return []
    if len(cleaned) < len(tags) * 0.4:
        return []
    return cleaned[:5]


async def generate_session_summary(
    events: list[dict],
    model_pool,
    prompts,
    *,
    max_summary_chars: int = 200,
) -> tuple[str, list[str]]:
    """用 fast 模型从事件历史生成会话摘要和标签。

    Args:
        events: 完整事件列表（Event.to_dict() 格式）。
        model_pool: ResourcePool 实例（用于 model_selector="fast"）。
        prompts: PromptLoader 实例。

    Returns:
        (task_summary, tags) — 如 ("用户查询天气并获取实时数据", ["天气", "搜索"])
    """
    if not events or model_pool is None:
        return ("", [])

    # 提取 user + assistant 文本（去掉工具输出的噪声）
    dialog_parts: list[str] = []
    for e in events:
        et = e.get("event_type", "")
        content = e.get("content", {})
        if not isinstance(content, dict):
            continue
        if et == "user_message":
            text = content.get("content", "")
            if text:
                dialog_parts.append(f"[用户]: {text[:500]}")
        elif et == "model_message":
            text = content.get("text", "")
            if text:
                dialog_parts.append(f"[助手]: {text[:500]}")

    dialog_text = "\n".join(dialog_parts[-20:])  # 最近 20 条对话
    if not dialog_text:
        return ("", [])

    try:
        template = prompts.get_tool_summary(
            "summary_system_template",
            (
                "你是会话摘要助手。根据对话内容生成一句话任务摘要（不超过{max_chars}字）和 2-4 个标签。\n"
                "标签要求：每个标签 1-8 个汉字或英文单词，简洁明确，不含任何标点符号、代码片段或特殊字符。\n"
                "输出格式：\n摘要：<一句话>\n标签：<tag1>, <tag2>, ..."
            ),
        )
        system_prompt = template.replace("{max_chars}", str(max_summary_chars))

        # 使用 model default max_tokens（1024），不再用 256 硬编码导致截断
        response = await model_pool.generate(
            model_selector="fast",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"对话内容：\n{dialog_text}"},
            ],
            stream=False,
        )
        text = (response.content or "").strip()
        if not text:
            return ("", [])

        # 先检查输出是否包含期望的格式标志，缺少则判废
        has_summary = "摘要" in text
        has_tags = "标签" in text
        if not has_summary and not has_tags:
            logger.warning("Session summary output missing expected format, discarding: %.100s", text)
            return ("", [])

        # 解析
        summary = ""
        tags: list[str] = []
        for line in text.split("\n"):
            line = line.strip()
            if (line.startswith("摘要：") or line.startswith("摘要:")):
                summary = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif (line.startswith("标签：") or line.startswith("标签:")):
                tags_str = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                raw_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                tags = _sanitize_tags(raw_tags)

        return (summary[:max_summary_chars], tags)

    except Exception as e:
        logger.warning("Session summary generation failed: %s", e)
        return ("", [])


# ── 内部工具 ────────────────────────────────────────────────────────────────

def _write_yaml(file_store, rel_path: str, data: dict) -> None:
    import yaml
    content = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
    file_store.write_atomic_sync(rel_path, content)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
