"""
Orchestration — the conductor layer.

Provides:
  - OrchestrationWorkflow: Deterministic Temporal Workflow that coordinates
    Harness (brain), Session (memory), and Sandbox (hands) through the agentic loop.
  - Worker entrypoint:     Boots the Temporal Worker, injects dependencies,
    and starts channel listeners.
"""
from .workflow import OrchestrationWorkflow
from .worker import start_worker, init_dependencies

__all__ = ["OrchestrationWorkflow", "start_worker", "init_dependencies"]
