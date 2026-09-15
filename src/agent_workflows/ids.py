"""Deterministic workflow addresses shared by commands and execution."""
import hashlib


def tool_execution_workflow_id(run_id: str, operation_id: str) -> str:
    identity = hashlib.sha256(f"{run_id}:{operation_id}".encode()).hexdigest()[:32]
    return f"hpagent-tool-{run_id}-{identity}"
