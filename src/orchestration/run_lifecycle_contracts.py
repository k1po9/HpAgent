"""Run lifecycle contracts. Queue names are deployment addresses, not source constraints."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, TypedDict

from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

WEB_LIFECYCLE_TASK_QUEUE = "hpagent-web-lifecycle"
WEB_AGENT_TASK_QUEUE = "hpagent-web-agent"
WEB_WORKFLOW_SCHEMA_VERSION = 1
WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS = 3000
WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS = 120
WEB_PREPARE_START_TO_CLOSE_SECONDS = 15
WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS = 15
WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS = 300
WEB_FINALIZE_START_TO_CLOSE_SECONDS = 20


@dataclass(frozen=True)
class RunLifecycleInput:
    """The complete, deliberately minimal, Workflow input contract."""

    schema_version: int
    run_id: str

    def validate(self) -> None:
        if self.schema_version != WEB_WORKFLOW_SCHEMA_VERSION:
            raise ApplicationError("unsupported Run lifecycle schema", non_retryable=True)
        if not self.run_id:
            raise ApplicationError("run_id is required", non_retryable=True)


class RunAuthority(TypedDict):
    run_id: str
    status: Literal["queued", "running", "cancelling", "cancelled", "succeeded", "failed"]


@dataclass(frozen=True)
class FailureInput:
    schema_version: int
    run_id: str
    error_code: str
    error_message: str


_LIFECYCLE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
_FINALIZE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=10,
)
_STABLE_FAILURE_MESSAGES = {
    "context_build_failed": "上下文构建失败。",
    "memory_isolation_violation": "长期记忆隔离校验失败。",
    "model_unavailable": "模型暂时不可用。",
    "model_timeout": "模型调用超时。",
    "model_request_invalid": "模型请求格式校验失败。",
    "model_response_invalid": "模型响应无法解析。",
    "model_connection_failed": "无法连接模型服务。",
    "model_read_timeout": "等待模型响应超时。",
    "model_access_denied": "模型服务拒绝访问。",
    "model_request_rejected": "模型服务拒绝请求。",
    "model_rate_limited": "模型服务额度不足或请求过多。",
    "model_http_error": "模型服务返回错误。",
    "account_model_entitlement_unavailable": "账户模型权限不可用。",
    "work_budget_exhausted": "工作累计预算已耗尽。",
    "run_budget_exhausted": "本次执行预算已耗尽。",
    "account_daily_model_budget_exhausted": "账户今日模型额度已耗尽。",
    "model_access_tier_denied": "当前账户无权访问所选模型。",
    "tool_failed": "工具执行失败。",
    "tool_timeout": "工具执行超时。",
    "side_effect_audit_unavailable": "副作用审计暂不可用。",
    "run_timeout": "执行总时长超时。",
    "workspace_recovery_required": "工作区需要人工恢复。",
}
