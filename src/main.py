"""
HpAgent —— 主入口，负责加载配置并启动智能体服务。

============================================================================
启动流程（共 2 步）
============================================================================

  1. AppConfig.from_yaml() → 从 config.yaml 加载全量结构化配置
  2. start_worker(app_config) → 组装依赖 → 连接 Temporal → 启动 Worker + 渠道监听

============================================================================
运行方式
============================================================================

  python -m src.main          # 模块方式运行
  python src/main.py          # 直接运行
  temporalite start --namespace default --ip 0.0.0.0  # 先启动 Temporal（开发用）
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# ── 项目根目录检测（日志在所有模块导入之前配置）──
# Docker: /app/main.py → /app/，本地: src/main.py → ../
_base = Path(__file__).resolve().parent
_config_dir = _base / "config"
if not _config_dir.exists():
    _config_dir = _base.parent / "config"
_project_root = _config_dir.parent

# ── 加载 .env 环境变量（必须在所有模块导入之前）──
try:
    from dotenv import load_dotenv
    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass

# ── 日志配置（数据路径锚定到项目根目录）──
from common.logging import setup_logging

_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_log_dir = Path(os.getenv("LOG_DIR", str(_project_root / ".data/logs")))
setup_logging(level=_log_level, log_dir=_log_dir)

logger = logging.getLogger("HpAgent")

from orchestration.config import AppConfig
from orchestration.worker import start_worker


async def main_async():
    """异步主流程: 加载配置 → 启动 Worker。"""
    config_path = _config_dir / "config.yaml"

    try:
        config = AppConfig.from_yaml(str(config_path))
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        return

    logger.info("=== HpAgent ===")
    if config.models.chat:
        logger.info("Model (chat): %s:%s", config.models.chat[0].provider, config.models.chat[0].model)
    logger.info("Temporal: %s", config.temporal.host)
    logger.info("Redis: %s", "enabled" if config.redis.url else "disabled")
    logger.info("Hindsight: %s", "enabled" if config.hindsight.enabled else "disabled")
    logger.info("Sandbox: time=%ds mem=%dMB", config.sandbox.time_limit, config.sandbox.memory_limit_mb)
    logger.info("Log dir: %s", _log_dir)

    # config 有 prompt、agent、model、temporal、redis、hindsight、sandbox 等字段，包含所有运行时参数
    await start_worker(config)


def main():
    """同步包装入口。"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
