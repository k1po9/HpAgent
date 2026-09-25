"""
ResourcePool —— 模型调用池，实现 IResources 接口。

核心职责:
  1. 管理多个模型客户端（ModelClient）的注册和退避。
  2. 支持退避链: 当主模型调用失败时自动切换到下一个备用模型。

退避链机制:
  - configure_fallback_group("default", ["anthropic:claude", "openai:gpt4"])
  - generate(model_selector="default") 时先尝试 anthropic:claude，
    失败自动切换到 openai:gpt4。
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from common.errors import ModelAPIError
from common.interfaces import IResources
from common.logging import log_event
from common.model_usage import canonical_model_usage
from common.token_counter import estimate_messages_tokens, estimate_tokens
from resources.account_daily_budget import AccountModelEntitlementUnavailable
from resources.model_budget_context import current_model_call
from resources.model_governance_errors import ModelAccessTierDenied

from .credentials import CredentialManager
from .model_client import ModelClient, ModelDispatchError

logger = logging.getLogger("HpAgent.ResourcePool")


class ResourcePool(IResources):
    """模型资源池 —— 多模型注册 + 退避链。

    用法::

        pool = ResourcePool(credential_manager)
        await pool.initialize_models()               # 加载凭据中的所有模型
        pool.configure_fallback_group("default", ["anthropic:claude", "openai:gpt4"])
        response = await pool.generate(model_selector="default", messages=[...])
    """

    def __init__(
        self, credential_manager: CredentialManager, *, entitlement_service: Any = None,
        budget_coordinator: Any = None, snapshot_repository: Any = None,
    ):
        self._credential_manager = credential_manager
        self._entitlements = entitlement_service
        self._budget = budget_coordinator
        self._snapshots = snapshot_repository
        self._model_clients: Dict[str, Any] = {}          # endpoint_id → {"client": ModelClient, ...}
        self._fallback_groups: Dict[str, List[str]] = {}  # group_name → [endpoint_id, ...]

    async def initialize_models(self) -> None:
        """从凭据管理器加载所有模型端点并注册到内部客户端池。

        在 Worker 启动时调用一次。遍历 CredentialManager 的端点列表，
        为每个端点创建 ModelClient 并注册到 _model_clients。
        同时构造默认退避组 "default"，按注册顺序包含所有模型。
        """
        endpoints = self._credential_manager.get_model_endpoint_list()
        if not endpoints:
            return

        client_ids = []
        for index, ep in enumerate(endpoints):
            # endpoint_id 表示配置实例，而不是远端模型身份。缺省 ID 也带序号，
            # 避免相同 provider/model 的不同参数互相覆盖。
            client_id = ep.endpoint_id or f"endpoint:{index}:{ep.provider}:{ep.model}"
            client_cfg: Dict[str, Any] = {
                "api_key": ep.api_key,
                "base_url": ep.base_url,
                "model": ep.model,
                "endpoint_id": client_id,
                "provider": ep.provider,
            }
            # 从 extra 字段传递 api_format / max_tokens / timeout / extra_body
            if ep.extra:
                if "api_format" in ep.extra:
                    client_cfg["api_format"] = ep.extra["api_format"]
                if "max_tokens" in ep.extra:
                    client_cfg["max_tokens"] = ep.extra["max_tokens"]
                if "timeout" in ep.extra:
                    client_cfg["timeout"] = ep.extra["timeout"]
                if "extra_body" in ep.extra and ep.extra["extra_body"]:
                    client_cfg["extra_body"] = ep.extra["extra_body"]
            client = ModelClient(config=client_cfg)
            self._model_clients[client_id] = {"client": client, "priority": 0}
            self._model_clients[client_id]["access_tier"] = ep.access_tier
            client_ids.append(client_id)
            logger.info(
                "Model endpoint registered: id=%s provider=%s model=%s "
                "format=%s max_tokens=%s timeout=%ss extra_body_keys=%s",
                client_id,
                ep.provider,
                ep.model,
                client_cfg.get("api_format", "anthropic"),
                client_cfg.get("max_tokens", 2048),
                client_cfg.get("timeout", 30.0),
                sorted((client_cfg.get("extra_body") or {}).keys()),
            )

        # 默认退避组: 按注册顺序包含所有模型
        if client_ids:
            self._fallback_groups["default"] = client_ids

    def configure_fallback_group(self, group_name: str, model_ids: List[str]) -> None:
        """批量配置退避链（从列表直接设置）。

        Args:
            group_name: 退避组名称（如 "chat"、"embedding"）。
            model_ids: 有序的模型 ID 列表。
        """
        if model_ids:
            self._fallback_groups[group_name] = list(model_ids)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model_selector: str = "default",
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        max_tokens: Optional[int] = None,
        latency_budget: Optional[float] = None,
    ) -> Any:
        """按退避链调用模型生成回复。

        退避逻辑:
          1. model_selector 可以是退避组名（"default"）或具体 model_id。
          2. 按顺序逐一尝试候选模型。
          3. ModelAPIError / ConnectionError / TimeoutError → 自动跳到下一个。
          4. 其他异常（如 TypeError、ValueError）→ 不隐藏，直接抛出。
          5. 所有模型都失败 → 抛出 ModelAPIError。

        Args:
            messages: LLM 标准 messages 列表。
            model_selector: 模型选择器（退避组名或 model_id）。
            tools: 工具定义列表。
            stream: 是否启用流式返回。

        Returns:
            ModelResponse 对象。

        Raises:
            ModelAPIError: 所有模型均调用失败。
        """
        # 解析选择器: 优先查找退避组，找不到则视为单模型 ID
        candidate_ids = self._fallback_groups.get(model_selector, [model_selector])
        last_error = None
        chain_start = time.monotonic()
        attempt = 0
        configured_candidates = 0
        tier_denied_candidates = 0
        call_context = current_model_call()
        governed = any((self._entitlements, self._budget, self._snapshots))
        if governed and not all((self._entitlements, self._budget, self._snapshots)):
            raise RuntimeError("model governance services are only partially configured")
        if governed and call_context is None:
            raise RuntimeError("production model call is missing ModelCallContext")
        if call_context is not None:
            call_ordinal, model_call_id = call_context.begin_logical_call()
            call_context.model_call_id = model_call_id

        for model_id in candidate_ids:
            attempt += 1
            model_info = self._model_clients.get(model_id)
            if not model_info:
                continue
            configured_candidates += 1
            client = model_info["client"]
            if call_context is not None:
                call_context.endpoint_id = model_id
                call_context.provider = getattr(client, "provider", None)
                call_context.model = getattr(client, "model", None)
                call_context.attempt = attempt
                call_context.failure_logged = False
                event_fields = dict(
                    artifact_id=call_context.artifact_id,
                    artifact_version_id=call_context.artifact_version_id,
                    source_run_id=str(call_context.run_id),
                    workflow_id=call_context.workflow_id,
                    model_call_id=str(model_call_id),
                    endpoint_id=model_id, provider=call_context.provider,
                    model=call_context.model, attempt=attempt,
                )
            t0 = time.monotonic()
            budget_operation_id = ""
            reservation: dict[str, int] | None = None
            should_settle = False
            quota_date = None
            prepared = None
            snapshot = None
            if governed and call_context is not None:
                lookup = await asyncio.to_thread(
                    self._entitlements.get, call_context.account_id
                )
                entitlement = getattr(lookup, "entitlement", None)
                if getattr(getattr(lookup, "state", None), "value", None) != "valid" or entitlement is None:
                    raise AccountModelEntitlementUnavailable("model entitlement unavailable")
                endpoint_tier = str(model_info.get("access_tier", "standard"))
                account_tier = str(entitlement.model_access_tier)
                if account_tier != "owner" and account_tier != endpoint_tier:
                    logger.info(
                        "Skipping endpoint %s: Account tier %s cannot use %s",
                        model_id, account_tier, endpoint_tier,
                    )
                    last_error = ModelAPIError("model endpoint access tier denied")
                    tier_denied_candidates += 1
                    continue
                try:
                    prepared = client.prepare_request(
                        messages, tools, stream, max_tokens=max_tokens,
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                body = prepared.body()
                budget_operation_id = call_context.attempt_operation_id(
                    model_call_id, attempt, model_id
                )
                input_tokens = estimate_messages_tokens(list(body.get("messages") or []))
                if body.get("tools"):
                    input_tokens += estimate_tokens(json.dumps(
                        body["tools"], ensure_ascii=False, sort_keys=True,
                    ))
                output_tokens = max(0, int(body.get("max_tokens", 0)))
                reservation = {
                    "model_input_tokens": input_tokens,
                    "model_output_tokens": max(0, output_tokens),
                    "model_total_tokens": input_tokens + max(0, output_tokens),
                    "model_calls": 1,
                }
                common_snapshot = dict(
                    account_id=call_context.account_id, run_id=call_context.run_id,
                    model_call_id=model_call_id, operation_id=budget_operation_id,
                    execution_attempt=call_context.execution_attempt,
                    call_ordinal=call_ordinal, fallback_attempt=attempt,
                    phase=call_context.phase,
                    entitlement_version=entitlement.version,
                )
                freeze = getattr(self._snapshots, "freeze", None)
                if freeze is not None:
                    snapshot = await asyncio.to_thread(
                        freeze, **common_snapshot, endpoint_id=prepared.endpoint_id,
                        provider=prepared.provider, model=prepared.model,
                        api_format=prepared.api_format, payload=body,
                        resolved_url=prepared.url,
                        serializer_version=prepared.serializer_version,
                    )
                else:
                    snapshot = await asyncio.to_thread(
                        self._snapshots.create, **common_snapshot, prepared=prepared,
                    )
                mutation = await asyncio.to_thread(
                    self._budget.reserve,
                    call_context.account_id,
                    call_context.run_id,
                    budget_operation_id,
                    reservation["model_total_tokens"],
                    reservation,
                    final_response=call_context.final_response,
                    snapshot_id=snapshot.snapshot_id,
                    expected_entitlement_version=entitlement.version,
                    endpoint_access_tier=endpoint_tier,
                )
                quota_date = mutation.account.quota_date
                should_settle = not (
                    getattr(mutation.account, "replayed", False)
                    and getattr(mutation.account, "state", "") in {"settled", "released"}
                )
            if call_context is not None and call_context.artifact_version_id:
                log_event(logger, logging.INFO, "artifact_model_call_started", "artifact", **event_fields)
            try:
                if prepared is None and hasattr(client, "prepare_request"):
                    prepared = client.prepare_request(messages, tools, stream, max_tokens)
                if prepared is not None and hasattr(client, "send_prepared"):
                    result = await client.send_prepared(prepared)
                else:
                    result = await client.generate(
                        messages=messages, tools=tools, stream=stream,
                        max_tokens=max_tokens,
                    )
                elapsed_ms = (time.monotonic() - t0) * 1000
                elapsed_s = (time.monotonic() - t0)
                if should_settle and reservation is not None:
                    usage = canonical_model_usage(
                        getattr(result, "usage", None),
                        messages=messages,
                        output_text=getattr(result, "content", None),
                    )
                    actual = {
                            "model_input_tokens": int(usage["input_tokens"]),
                            "model_output_tokens": int(usage["output_tokens"]),
                            "model_total_tokens": int(usage["total_tokens"]),
                            "model_calls": 1,
                    }
                    if governed:
                        await asyncio.to_thread(
                            self._budget.settle, call_context.account_id,
                            call_context.run_id, budget_operation_id,
                            int(usage["total_tokens"]), actual,
                            str(usage["usage_source"]), quota_date=quota_date,
                        )
                    should_settle = False

                # 延迟预算回退：若当前模型响应慢但后面还有候选，主动超时触发 fallback
                if latency_budget and elapsed_s > latency_budget:
                    if attempt < len(candidate_ids):
                        logger.warning(
                            "Endpoint %s exceeded latency budget (%.1fs > %.1fs), "
                            "falling back to next candidate",
                            model_id, elapsed_s, latency_budget,
                        )
                        raise TimeoutError(
                            f"latency budget exceeded: {elapsed_s:.1f}s > {latency_budget:.1f}s"
                        )

                chain_elapsed = (time.monotonic() - chain_start) * 1000
                # [TIMING] 临时日志，标记每次成功调用的耗时
                logger.info(
                    "[TIMING] group=%s attempt=%d/%d endpoint=%s latency=%.0fms chain_total=%.0fms",
                    model_selector, attempt, len(candidate_ids),
                    model_id, elapsed_ms, chain_elapsed,
                )
                # Attach the selected endpoint as operational metadata.  The
                # Brain/Trace layer reads these fields without retaining model
                # response content or reasoning.
                try:
                    result.endpoint_id = model_id
                    result.model = getattr(client, "model", None)
                    result.provider = getattr(client, "provider", None)
                    if snapshot is not None:
                        result.model_call_id = str(model_call_id)
                        result.snapshot_id = str(snapshot.snapshot_id)
                        digest = getattr(snapshot, "content_hash", None)
                        result.content_hash = (
                            digest.hex() if isinstance(digest, bytes)
                            else getattr(snapshot, "content_hash_hex", str(digest or ""))
                        )
                        result.fallback_attempt = attempt
                        result.provider_outcome = "succeeded"
                except (AttributeError, TypeError):
                    pass
                if call_context is not None and call_context.artifact_version_id:
                    log_event(logger, logging.INFO, "artifact_model_call_succeeded", "artifact",
                              **event_fields)
                return result
            except (ModelAPIError, ConnectionError, TimeoutError) as e:
                if should_settle and reservation is not None:
                    if governed and isinstance(e, ModelDispatchError):
                        await asyncio.to_thread(
                            self._budget.settle, call_context.account_id,
                            call_context.run_id, budget_operation_id,
                            reservation["model_total_tokens"], reservation, "estimated",
                            quota_date=quota_date,
                        )
                    elif governed:
                        await asyncio.to_thread(
                            self._budget.release, call_context.account_id,
                            call_context.run_id, budget_operation_id,
                            quota_date=quota_date,
                        )
                    should_settle = False
                elapsed = (time.monotonic() - t0) * 1000
                chain_elapsed = (time.monotonic() - chain_start) * 1000
                logger.warning(
                    "DEGRADATION: endpoint %s failed (%s) → trying next in chain [%s] "
                    "(attempt %d/%d, attempt_latency=%.0fms chain_total=%.0fms)",
                    model_id, type(e).__name__, model_selector,
                    attempt, len(candidate_ids), elapsed, chain_elapsed,
                )
                if call_context is not None and call_context.artifact_version_id:
                    from web_artifacts.generator import classify_model_failure
                    failure = classify_model_failure(e)
                    log_event(logger, logging.WARNING, "artifact_model_call_failed", "artifact",
                              **event_fields, exception_type=failure.exception_type,
                              error_code=failure.code, retryable=failure.retryable)
                    call_context.failure_logged = True
                last_error = e
                continue
            except Exception:
                if should_settle and reservation is not None:
                    if governed:
                        await asyncio.to_thread(
                            self._budget.release, call_context.account_id,
                            call_context.run_id, budget_operation_id, quota_date=quota_date,
                        )
                # 不可恢复错误 → 直接抛出
                raise

        chain_elapsed = (time.monotonic() - chain_start) * 1000
        if configured_candidates and tier_denied_candidates == configured_candidates:
            raise ModelAccessTierDenied("no endpoint permitted by account model tier")
        if last_error:
            raise ModelAPIError(
                f"All models in group '{model_selector}' failed (chain_total=%.0fms)."
                % chain_elapsed
            ) from last_error
        raise ModelAPIError(f"No models available for selector '{model_selector}'.")
