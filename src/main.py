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
import sys
from pathlib import Path

logger = logging.getLogger("HpAgent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from orchestration.config import AppConfig
from orchestration.worker import start_worker


async def main_async():
    """异步主流程: 加载配置 → 启动 Worker。"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"

    try:
        config = AppConfig.from_yaml(str(config_path))
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}")
        print("Create config/config.yaml from the template.")
        return

    print("\n=== HpAgent ===")
    if config.models.chat:
        print(f"Model (chat): {config.models.chat[0].provider}:{config.models.chat[0].model}")
    if config.models.embedding:
        print(f"Model (embedding): {config.models.embedding[0].provider}:{config.models.embedding[0].model}")
    print(f"Temporal: {config.temporal.host}")
    print(f"Redis: {'enabled' if config.redis.url else 'disabled'}")
    print(f"Hindsight: {'enabled' if config.hindsight.enabled else 'disabled'}")
    print(f"Sandbox: time={config.sandbox.time_limit}s, mem={config.sandbox.memory_limit_mb}MB")
    await start_worker(config)


def main():
    """同步包装入口。"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
