"""Compact capability orchestration contracts; safe for the Workflow sandbox."""

from dataclasses import dataclass

from .contracts import ToolExecutionInput, ToolExecutionResult

CAPABILITY_TOOL_PREFIX = "capability__"


@dataclass(frozen=True)
class CapabilityCall:
    tool: ToolExecutionInput
    account_id: str
    run_id: str
    execution_id: str
    operation_id: str
    lease_token: int = 0
    execution_attempt: int = 1


@dataclass(frozen=True)
class CapabilityExecutionInput:
    account_id: str
    run_id: str
    execution_id: str
    invocation_id: str
    workflow_name: str
    max_duration_seconds: int
    max_attempts: int
    operation_id: str
    lease_token: int = 0
    execution_attempt: int = 1
    schema_version: int = 1


@dataclass(frozen=True)
class CapabilityPrepared:
    child: CapabilityExecutionInput | None = None
    replay: ToolExecutionResult | None = None
    result_committed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CapabilityFinish:
    call: CapabilityCall
    error: str | None = None
    account_id: str = ""
    run_id: str = ""
    execution_id: str = ""
    operation_id: str = ""
    lease_token: int = 0
    execution_attempt: int = 1


@dataclass(frozen=True)
class CapabilityClose:
    child: CapabilityExecutionInput
    status: str
