"""
HpAgent —— 主入口，负责启动完整智能体服务。

============================================================================
启动流程（共 3 步）
============================================================================

  1. load_config() → 从 config.yaml 加载 AppConfig
     - api_key / base_url / model: 模型连接配置
     - temporal_host: Temporal Server 地址
     - max_history_turns / max_turns: 对话轮次限制
  2. main_async() → 组装 worker_config → 调用 start_worker()
  3. start_worker() → 初始化所有依赖 → 连接 Temporal → 启动 Worker + 渠道监听
     （详见 orchestration/worker.py）

============================================================================
运行方式
============================================================================

  python -m src.main          # 模块方式运行
  python src/main.py          # 直接运行（__main__ 守卫）
  temporalite start --namespace default --ip 0.0.0.0  # 先启动 Temporal（开发用）

============================================================================
配置示例（config/config.yaml）
============================================================================

  model:
    api_key: "sk-xxx"
    base_url: "https://api.anthropic.com/v1"
    model: "claude-sonnet-4-6"
  app:
    max_history_turns: 20
    max_turns: 50
    temporal_host: "localhost:7233"
"""
import asyncio
import logging
import sys
import yaml
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("HpAgent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from orchestration.worker import start_worker


@dataclass
class AppConfig:
    """应用配置的强类型表示，从 config.yaml 解析而来。

    Attributes:
        api_key: 模型 API 密钥（如 Anthropic API Key）。
        base_url: 模型 API 基础 URL（如 https://api.anthropic.com/v1）。
        model: 模型名称（如 claude-sonnet-4-6）。
        max_history_turns: 上下文窗口内保留的最大历史轮次。
        max_turns: 单次 agentic loop 的最大工具调用轮数（防止死循环）。
        temporal_host: Temporal Server 地址（host:port 格式，如 localhost:7233）。
    """
    api_key: str
    base_url: str
    model: str
    max_history_turns: int
    max_turns: int
    temporal_host: str


def load_config(config_path: str = "../config/config.yaml") -> AppConfig:
    """从 YAML 配置文件加载应用配置。

    查找路径: 默认相对于 src/ 的 ../config/config.yaml。
    若文件不存在则抛出 FileNotFoundError，由调用方提示用户创建配置。

    Args:
        config_path: YAML 配置文件路径。

    Returns:
        AppConfig 实例，包含 model 和 app 两个配置段。

    Raises:
        FileNotFoundError: 配置文件不存在。
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    model_config = config_data["model"]
    app_config = config_data["app"]
    return AppConfig(
        api_key=model_config["api_key"],
        base_url=model_config["base_url"],
        model=model_config["model"],
        max_history_turns=app_config["max_history_turns"],
        max_turns=app_config["max_turns"],
        temporal_host=app_config["temporal_host"],
    )


async def main_async():
    """异步主流程: 加载配置 → 启动 Worker。

    包含配置缺失的友好提示，避免裸 Traceback。
    """
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please create a config.yaml file with your settings.")
        return

    # 组装 Worker 所需的最小配置字典
    worker_config = {
        "api_key": config.api_key,
        "base_url": config.base_url,
        "model": config.model,
        "temporal_host": config.temporal_host,
    }

    print("\n=== HpAgent ===")
    print(f"Temporal Server: {config.temporal_host}")
    print("Task Queue: hpagent-task-queue")
    await start_worker(worker_config)


def main():
    """同步包装入口 —— 兼容直接 python main.py 调用。"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
