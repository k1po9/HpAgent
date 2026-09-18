"""Stable, non-retryable model-access governance failures."""
from __future__ import annotations

from dataclasses import dataclass


class ModelGovernanceError(RuntimeError):
    code = "model_governance_denied"
    safe_message = "模型访问被拒绝。"


class ModelAccessTierDenied(ModelGovernanceError):
    code = "model_access_tier_denied"
    safe_message = "当前账户无权访问所选模型。"


@dataclass(frozen=True)
class ModelGovernanceFailure:
    code: str
    safe_message: str


def classify_model_governance_failure(exc: BaseException) -> ModelGovernanceFailure | None:
    if isinstance(exc, ModelGovernanceError):
        return ModelGovernanceFailure(exc.code, exc.safe_message)
    return None
