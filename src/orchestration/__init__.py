"""
Orchestration —— 纯编排层。

Temporal Workflow 不持有任何业务数据，只做循环控制 + 信号路由。
Agentic loop 全部在 HarnessRunner 内部完成。

模块结构：
  - workflow.py: OrchestrationWorkflow —— 纯编排（循环 / 信号 / 查询）
  - worker.py:   依赖组装 + Temporal Worker 启动 + 渠道监听
"""
from .workflow import OrchestrationWorkflow
from .worker import start_worker

__all__ = ["OrchestrationWorkflow", "start_worker"]
