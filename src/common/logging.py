"""
Centralized logging setup —— 双 sink：控制台（stderr）+ 结构化 JSONL 文件。

Usage:
    from common.logging import setup_logging
    setup_logging(level=logging.DEBUG, log_dir=Path("data/logs"))

Log files:
    data/logs/hpagent.jsonl     全量结构化 JSON（每行一条记录，默认 DEBUG）
    data/logs/hpagent-error.log 仅 ERROR+，快速定位故障
"""
from __future__ import annotations

import json
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """结构化 JSON 行格式 —— 每行一条独立 JSON，方便 jq/grep 处理。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exc"] = str(record.exc_info[1])
        if record.args and isinstance(record.args, dict):
            payload["extra"] = record.args
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    level: int = logging.INFO,
    log_dir: Path | str = Path("data/logs"),
) -> None:
    """配置全局日志系统。

    必须在任何模块级 logger 获取之前调用。

    Args:
        level: 控制台和文件的日志级别。
        log_dir: 日志文件目录，None 时不写文件。
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handler 各自控制级别

    # 清除已有 handler（防止重复调用）
    root.handlers.clear()

    # ── 控制台 sink ──
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(console)

    # ── 文件 sink ──
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # JSONL 全量日志（每天轮转，保留 30 天）
        json_handler = TimedRotatingFileHandler(
            str(log_path / "hpagent.jsonl"),
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        json_handler.setLevel(logging.DEBUG)
        json_handler.setFormatter(_JsonFormatter())
        root.addHandler(json_handler)

        # 纯文本错误日志（只记录 ERROR+）
        err_handler = TimedRotatingFileHandler(
            str(log_path / "hpagent-error.log"),
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        err_handler.setLevel(logging.ERROR)
        err_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(err_handler)
