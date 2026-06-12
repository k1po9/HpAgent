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
import logging
import time
from typing import Dict, Any, Optional, List
from .credentials import CredentialManager
from common.interfaces import IResources
from common.errors import ModelAPIError
from .model_client import ModelClient

logger = logging.getLogger("HpAgent.ResourcePool")


class ResourcePool(IResources):
    """模型资源池 —— 多模型注册 + 退避链。

    用法::

        pool = ResourcePool(credential_manager)
        await pool.initialize_models()               # 加载凭据中的所有模型
        pool.configure_fallback_group("default", ["anthropic:claude", "openai:gpt4"])
        response = await pool.generate(model_selector="default", messages=[...])
    """

    def __init__(self, credential_manager: CredentialManager):
        self._credential_manager = credential_manager
        self._model_clients: Dict[str, Any] = {}          # model_id("{provider}:{model_name}") → {"client":ModelClient, priority":int}
        self._fallback_groups: Dict[str, List[str]] = {}  # group_name → [model_id, ...]

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
        for ep in endpoints:
            client_id = f"{ep.provider}:{ep.model}"
            client_cfg: Dict[str, Any] = {
                "api_key": ep.api_key,
                "base_url": ep.base_url,
                "model": ep.model,
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
            client_ids.append(client_id)

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

        for model_id in candidate_ids:
            attempt += 1
            model_info = self._model_clients.get(model_id)
            if not model_info:
                continue
            client = model_info["client"]
            t0 = time.monotonic()
            try:
                result = await client.generate(
                    messages=messages, tools=tools, stream=stream,
                    max_tokens=max_tokens,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                elapsed_s = (time.monotonic() - t0)

                # 延迟预算回退：若当前模型响应慢但后面还有候选，主动超时触发 fallback
                if latency_budget and elapsed_s > latency_budget:
                    if attempt < len(candidate_ids):
                        logger.warning(
                            "Model %s exceeded latency budget (%.1fs > %.1fs), "
                            "falling back to next candidate",
                            model_id, elapsed_s, latency_budget,
                        )
                        raise TimeoutError(
                            f"latency budget exceeded: {elapsed_s:.1f}s > {latency_budget:.1f}s"
                        )

                chain_elapsed = (time.monotonic() - chain_start) * 1000
                # [TIMING] 临时日志，标记每次成功调用的耗时
                logger.info(
                    "[TIMING] %s attempt=%d/%d model=%s latency=%.0fms chain_total=%.0fms",
                    model_selector, attempt, len(candidate_ids),
                    model_id, elapsed_ms, chain_elapsed,
                )
                return result
            except (ModelAPIError, ConnectionError, TimeoutError) as e:
                elapsed = (time.monotonic() - t0) * 1000
                chain_elapsed = (time.monotonic() - chain_start) * 1000
                logger.warning(
                    "DEGRADATION: model %s failed (%s) → trying next in chain [%s] "
                    "(attempt %d/%d, attempt_latency=%.0fms chain_total=%.0fms)",
                    model_id, e, model_selector,
                    attempt, len(candidate_ids), elapsed, chain_elapsed,
                )
                last_error = e
                continue
            except Exception:
                # 不可恢复错误 → 直接抛出
                raise

        chain_elapsed = (time.monotonic() - chain_start) * 1000
        if last_error:
            raise ModelAPIError(
                f"All models in group '{model_selector}' failed (chain_total=%.0fms)."
                % chain_elapsed
            ) from last_error
        raise ModelAPIError(f"No models available for selector '{model_selector}'.")
