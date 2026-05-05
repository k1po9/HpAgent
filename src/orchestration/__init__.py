"""
Orchestration —— 指挥层。

在"手脑分离"架构中，Orchestration 是"指挥"，通过 Temporal Workflow
协调 Harness（大脑）、Session（记忆）、Sandbox（双手）三层的运转。

模块结构：
  - workflow.py: OrchestrationWorkflow —— 长期运行的确定性编排核心
  - worker.py:   Temporal Worker 启动入口 + 依赖初始化 + 渠道监听

Workflow 的生命周期：
  1. start_workflow(user_message) → Workflow 启动
  2. Workflow.run() 进入 agentic loop（context → model → tools → response）
  3. 后续消息通过 signal (new_message) 入队
  4. wait_condition 等待新消息或取消信号
  5. cancel_session signal → Workflow 终止
"""
from .workflow import OrchestrationWorkflow
from .worker import start_worker, init_dependencies

__all__ = ["OrchestrationWorkflow", "start_worker", "init_dependencies"]
