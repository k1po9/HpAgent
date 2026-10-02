"""Bounded delegation contracts; no domain ownership or independent budget."""

from dataclasses import dataclass, field

from .contracts import AgentRunInput, ToolExecutionInput, ToolExecutionResult

DELEGATE_TOOL = "delegate_work"
MAX_BRANCHES = 3
MAX_BRANCH_ATTEMPTS = 2
NON_RETRYABLE_BRANCH_ERRORS = frozenset(
    {
        "run_budget_exhausted",
        "work_budget_exhausted",
        "account_daily_budget_exhausted",
        "stale_fencing_token",
        "run_not_executable",
        "delegation_scope_denied",
    }
)

DELEGATE_MANIFEST = {
    "type": "function",
    "function": {
        "name": DELEGATE_TOOL,
        "description": "Once per Work Run, investigate up to three independent directions in isolated contexts. "
        "Only read tools and explicitly listed resources are delegated. Required branch failure prevents completion. "
        "Return summaries, exact result references and gaps to the coordinator; no child can delegate again.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "branches": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_BRANCHES,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "branch_key": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,39}$"},
                            "objective": {"type": "string", "maxLength": 2000},
                            "constraints": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 10,
                            },
                            "output_contract": {"type": "string", "maxLength": 1000},
                            "node_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 40,
                            },
                            "file_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 40,
                            },
                            "tool_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 20,
                            },
                            "required": {"type": "boolean"},
                        },
                        "required": [
                            "branch_key",
                            "objective",
                            "constraints",
                            "output_contract",
                            "node_ids",
                            "file_ids",
                            "tool_names",
                            "required",
                        ],
                    },
                }
            },
            "required": ["branches"],
        },
    },
}


@dataclass(frozen=True)
class DelegationInput:
    tool: ToolExecutionInput
    branch_attempt: int = 1
    execution_id: str = field(kw_only=True)
    run_id: str = field(kw_only=True)
    account_id: str = field(kw_only=True)
    operation_id: str = field(kw_only=True)
    lease_token: int = 0
    execution_attempt: int = 1


@dataclass(frozen=True)
class DelegationPrepared:
    children: tuple[AgentRunInput, ...]
    replay: ToolExecutionResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class DelegationFinishInput:
    tool: ToolExecutionInput
    execution_id: str = field(kw_only=True)
    run_id: str = field(kw_only=True)
    account_id: str = field(kw_only=True)
    operation_id: str = field(kw_only=True)
    lease_token: int = 0
    execution_attempt: int = 1


@dataclass(frozen=True)
class BranchFinishInput:
    schema_version: int
    execution_id: str
    run_id: str
    account_id: str
    status: str
    result_ref: str | None = None
    error_code: str | None = None
