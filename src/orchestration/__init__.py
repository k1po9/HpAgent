"""
Orchestration —— 纯编排层。

Temporal Workflow 不持有任何业务数据，只做循环控制 + 信号路由。
单轮 agentic loop 由 TurnOrchestrator 编排。

模块结构：
  - workflow.py: OrchestrationWorkflow —— 纯编排（循环 / 信号 / 查询）
  - worker.py:   依赖组装 + Temporal Worker 启动 + 渠道监听
"""
__all__ = ["OrchestrationWorkflow", "start_worker"]


def __getattr__(name: str):
    """Keep public imports lazy so Workflow sandbox imports stay isolated."""
    if name == "OrchestrationWorkflow":
        from .workflow import OrchestrationWorkflow

        return OrchestrationWorkflow
    if name == "start_worker":
        from .worker import start_worker

        return start_worker
    raise AttributeError(name)
