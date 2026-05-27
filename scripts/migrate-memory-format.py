#!/usr/bin/env python3
"""
Hindsight 记忆格式迁移脚本 —— 旧格式 → 新格式（v0.6.1 best-practice 合规）。

旧格式特征:
  - document_id: "{session_id}-{i}" （逐条独立）
  - timestamp:   缺失
  - context:     "role=user" / "role=assistant"
  - tags:        ["user:{id}", "session:{id}"]
  - metadata:    {role, session_id}

新格式特征:
  - document_id: "session:{session_id}" （会话级全文替换）
  - timestamp:   ISO 8601
  - context:     渠道感知描述（QQ group chat / private chat / ...）
  - tags:        [user, session, channel, scope, group]
  - metadata:    {role, session_id, sender_name}

用法:
  # 仅预览（默认 dry-run）
  python scripts/migrate-memory-format.py --backup-dir .data/sessions

  # 执行本地 JSONL 迁移
  python scripts/migrate-memory-format.py --backup-dir .data/sessions --apply

  # 同时清理 Hindsight 中的旧格式记忆（需 Hindsight API 可达）
  python scripts/migrate-memory-format.py --backup-dir .data/sessions --apply --hindsight-url http://localhost:8001 --cleanup-hindsight

  # 仅统计，不迁移
  python scripts/migrate-memory-format.py --backup-dir .data/sessions --only-stats

迁移流程:
  1. 扫描 backup_dir 下的 *.jsonl 文件
  2. 逐行解析，检测是否为旧格式
  3. 输出旧格式统计 → 新格式预览
  4. --apply 时写入新格式文件（*.migrated.jsonl）
  5. --cleanup-hindsight 时通过 Hindsight API 删除旧格式记忆
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx


def detect_format(item: dict) -> str:
    """检测单条记忆的格式版本。"""
    doc_id = item.get("document_id", "")
    has_timestamp = bool(item.get("timestamp"))
    context = item.get("context", "")
    tags = item.get("tags", [])

    is_old = (
        not has_timestamp
        and not doc_id.startswith("session:")
        and context.startswith("role=")
    )
    if is_old:
        return "old"
    if has_timestamp and doc_id.startswith("session:") and not context.startswith("role="):
        return "new"
    return "mixed"


def migrate_item(item: dict, session_id: str, channel_type: str = "") -> dict:
    """将单条记忆从旧格式迁移到新格式。"""
    migrated = dict(item)
    migrated["document_id"] = f"session:{session_id}"
    if not migrated.get("timestamp"):
        migrated["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_context = migrated.get("context", "")
    role = migrated.get("metadata", {}).get("role", "user")
    if old_context.startswith("role="):
        migrated["context"] = _build_context(channel_type)
    tags = list(migrated.get("tags", []))
    if channel_type:
        ch_tag = f"channel:{channel_type}"
        if ch_tag not in tags:
            tags.append(ch_tag)
    if "scope:" not in " ".join(tags):
        tags.append("scope:private")
    migrated["tags"] = tags
    return migrated


def _build_context(channel_type: str) -> str:
    if channel_type == "napcat":
        return "QQ napcat chat"
    if channel_type == "console":
        return "Console CLI chat"
    if channel_type == "web":
        return "Web chat"
    return "chat"


async def _cleanup_hindsight_memories(
    base_url: str, document_ids: list[str], api_key: str = ""
) -> int:
    """通过 Hindsight API 删除指定 document_id 的记忆。

    Returns:
        成功删除的 document 数量。
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    deleted = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for doc_id in document_ids:
            try:
                resp = await client.delete(
                    f"{base_url.rstrip('/')}/v1/default/banks/hpagent/memories/{doc_id}",
                    headers=headers,
                )
                if resp.status_code < 400:
                    deleted += 1
                    print(f"  [Hindsight] deleted: {doc_id}")
                else:
                    print(f"  [Hindsight] skip {doc_id}: HTTP {resp.status_code}")
            except Exception as e:
                print(f"  [Hindsight] error deleting {doc_id}: {e}")
    return deleted


def _collect_old_document_ids(records: list[dict]) -> set[str]:
    """从备份记录中收集旧格式的 document_id 列表。"""
    ids: set[str] = set()
    for rec in records:
        events = rec.get("events") or []
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if detect_format(ev) == "old":
                doc_id = ev.get("document_id", "")
                if doc_id:
                    ids.add(doc_id)
    return ids


def main():
    parser = argparse.ArgumentParser(description="Hindsight 记忆格式迁移工具")
    parser.add_argument("--backup-dir", default=".data/sessions", help="JSONL 备份目录")
    parser.add_argument("--channel-type", default="", help="渠道类型（napcat/console/web）")
    parser.add_argument("--only-stats", action="store_true", help="仅统计，不迁移也不写文件")
    parser.add_argument("--apply", action="store_true", help="执行迁移并写入新文件")
    parser.add_argument("--hindsight-url", default="", help="Hindsight API 地址（用于清理旧格式记忆）")
    parser.add_argument("--cleanup-hindsight", action="store_true", help="通过 Hindsight API 删除旧格式记忆")
    parser.add_argument("--api-key", default="", help="Hindsight API 密钥")
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    if not backup_dir.exists():
        print(f"[ERROR] Backup dir not found: {backup_dir}")
        sys.exit(1)

    jsonl_files = sorted(backup_dir.glob("*.jsonl"))
    jsonl_files = [f for f in jsonl_files if ".migrated" not in f.name]

    if not jsonl_files:
        print(f"[INFO] No JSONL files found in {backup_dir}")
        return

    print(f"[INFO] Found {len(jsonl_files)} backup file(s)\n")

    total_records = 0
    old_count = 0
    new_count = 0
    mixed_count = 0
    all_old_doc_ids: set[str] = set()

    for filepath in jsonl_files:
        session_id = filepath.stem
        records = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"[WARN] Skip invalid JSON in {filepath.name}: {e}")

        file_old = 0
        file_new = 0
        file_mixed = 0

        for rec in records:
            events = rec.get("events") or []
            if not isinstance(events, list):
                continue
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                fmt = detect_format(ev)
                total_records += 1
                if fmt == "old":
                    old_count += 1
                    file_old += 1
                elif fmt == "new":
                    new_count += 1
                    file_new += 1
                else:
                    mixed_count += 1
                    file_mixed += 1

        print(f"  {filepath.name}: old={file_old} new={file_new} mixed={file_mixed}")

        if file_old > 0:
            all_old_doc_ids.update(_collect_old_document_ids(records))

        if args.apply and file_old > 0 and not args.only_stats:
            migrated_records = []
            for rec in records:
                events = rec.get("events") or []
                new_events = []
                for ev in events:
                    if not isinstance(ev, dict):
                        new_events.append(ev)
                        continue
                    if detect_format(ev) == "old":
                        new_events.append(migrate_item(ev, session_id, args.channel_type))
                    else:
                        new_events.append(ev)
                rec["events"] = new_events
                rec["_migrated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                migrated_records.append(rec)

            out_path = backup_dir / f"{session_id}.migrated.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                for rec in migrated_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  → wrote {out_path.name}")

    print(f"\n[SUMMARY] total={total_records} old={old_count} new={new_count} mixed={mixed_count}")

    if args.only_stats:
        if old_count > 0:
            print(f"[INFO] {old_count} old-format memories across {len(all_old_doc_ids)} unique document_ids")
        return

    if old_count > 0 and not args.apply:
        print("[INFO] Re-run with --apply to write migrated files")

    # ── Hindsight API 清理 ──
    if args.cleanup_hindsight and args.hindsight_url and all_old_doc_ids:
        print(f"\n[Hindsight Cleanup] Deleting {len(all_old_doc_ids)} old-format documents...")
        deleted = asyncio.run(
            _cleanup_hindsight_memories(args.hindsight_url, sorted(all_old_doc_ids), args.api_key)
        )
        print(f"[Hindsight Cleanup] Deleted {deleted}/{len(all_old_doc_ids)} documents")
    elif args.cleanup_hindsight:
        print("\n[Hindsight Cleanup] Skipped: --hindsight-url required or no old documents found")


if __name__ == "__main__":
    main()
