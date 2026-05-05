"""
ResourcePool —— 模型调用池，实现 IResources 接口。

核心职责:
  1. 管理多个模型客户端（ModelClient）的注册和退避。
  2. 支持退避链: 当主模型调用失败时自动切换到下一个备用模型。
  3. 支持代理 HTTP 请求（统一认证注入出口）。

退避链机制:
  - configure_fallback("default", "anthropic:claude", "openai:gpt4")
  - generate(model_selector="default") 时先尝试 anthropic:claude，
    失败自动切换到 openai:gpt4。
"""
from typing import Dict, Any, Optional, List
import json
from .credentials import CredentialManager
from common.interfaces import IResources
from common.errors import ModelAPIError, ValidationError


class ResourcePool(IResources):
    """模型资源池 —— 多模型注册 + 退避链 + 代理请求。

    用法::

        pool = ResourcePool(credential_manager)
        await pool.initialize_models()               # 加载凭据中的所有模型
        await pool.configure_fallback("default", "anthropic:claude", "openai:gpt4")
        response = await pool.generate(model_selector="default", messages=[...])
    """

    def __init__(self, credential_manager: CredentialManager = None):
        self._credential_manager = credential_manager
        self._model_clients: Dict[str, Any] = {}          # model_id → {"client": ..., "priority": int}
        self._fallback_groups: Dict[str, List[str]] = {}  # group_name → [model_id, ...]
        self._storage_path: Optional[str] = None

    def set_credential_manager(self, credential_manager):
        """注入凭据管理器。"""
        self._credential_manager = credential_manager

    def set_storage_path(self, path: str):
        """设置存储路径（预留，当前未使用）。"""
        self._storage_path = path

    async def initialize_models(self) -> None:
        """从凭据管理器加载所有模型端点并注册到内部客户端池。

        在 Worker 启动时调用一次。遍历 CredentialManager 的端点列表，
        为每个端点创建 ModelClient 并注册到 _model_clients。
        同时构造默认退避组 "default"，按注册顺序包含所有模型。
        """
        if not self._credential_manager:
            return
        endpoints = self._credential_manager.get_model_endpoint_list()
        if not endpoints:
            return

        from .model_client import ModelClient

        client_ids = []
        for ep in endpoints:
            client_id = f"{ep.provider}:{ep.model}"
            client = ModelClient(config={
                "api_key": ep.api_key,
                "base_url": ep.base_url,
                "model": ep.model,
            })
            self._model_clients[client_id] = {"client": client, "priority": 0}
            client_ids.append(client_id)

        # 默认退避组: 按注册顺序包含所有模型
        if client_ids:
            self._fallback_groups["default"] = client_ids

    async def get_credential(self, resource_id: str, scope: List[str]) -> str:
        """获取临时访问 token。"""
        if not self._credential_manager:
            raise ValidationError("credential_manager", "Not initialized")
        return self._credential_manager.issue_temp_token(resource_id, scope)

    async def proxy_request(
        self,
        target_url: str,
        method: str,
        resource_id: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """代理 HTTP 请求（统一出口，支持 token 认证注入）。

        流程:
          1. 从 headers 中提取 token_id。
          2. 校验 token 有效性。
          3. 剥离 Authorization 头（避免泄漏到外部）。
          4. 发起 HTTP 请求并返回 {status_code, body, headers}。
        """
        if not self._credential_manager:
            raise ValidationError("credential_manager", "Not initialized")
        token_data = None
        if headers and "Authorization" in headers:
            token_id = headers["Authorization"].replace("Bearer ", "")
            token_data = self._credential_manager.validate_token(token_id)
        if not token_data:
            raise ValidationError("authorization", "Invalid or expired token")

        request_headers = headers or {}
        request_headers.pop("Authorization", None)  # 不对外泄漏

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                if method.upper() == "GET":
                    response = await client.get(target_url, headers=request_headers)
                elif method.upper() == "POST":
                    response = await client.post(target_url, headers=request_headers, json=body)
                elif method.upper() == "PUT":
                    response = await client.put(target_url, headers=request_headers, json=body)
                elif method.upper() == "DELETE":
                    response = await client.delete(target_url, headers=request_headers)
                else:
                    raise ValidationError("method", f"Unsupported HTTP method: {method}")
                response.raise_for_status()
                return {
                    "status_code": response.status_code,
                    "body": response.json() if response.text else None,
                    "headers": dict(response.headers),
                }
            except httpx.HTTPStatusError as e:
                raise ModelAPIError(reason=str(e), status_code=e.response.status_code)
            except Exception as e:
                raise ModelAPIError(reason=str(e))

    # ══════════════════════════════════════════════════════════════════════
    # 模型管理 —— 注册和退避链
    # ══════════════════════════════════════════════════════════════════════

    async def register_model(self, model_id: str, client: Any, priority: int = 0) -> None:
        """手动注册一个模型客户端。

        Args:
            model_id: 模型标识符。
            client: 模型客户端实例（需实现 generate 方法）。
            priority: 优先级（数字越小越优先，默认 0）。
        """
        self._model_clients[model_id] = {"client": client, "priority": priority}

    async def configure_fallback(self, group_name: str, primary: str, *fallbacks: str) -> None:
        """配置退避链。

        Args:
            group_name: 退避组名称（如 "default"、"fast"、"cheap"）。
            primary: 主模型 ID。
            *fallbacks: 备用模型 ID 序列（按优先级降序）。
        """
        self._fallback_groups[group_name] = [primary] + list(fallbacks)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model_selector: str = "default",
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
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

        for model_id in candidate_ids:
            model_info = self._model_clients.get(model_id)
            if not model_info:
                continue
            client = model_info["client"]
            try:
                return await client.generate(
                    messages=messages, tools=tools, stream=stream
                )
            except (ModelAPIError, ConnectionError, TimeoutError) as e:
                # 可恢复错误 → 记录并尝试下一个
                last_error = e
                continue
            except Exception:
                # 不可恢复错误 → 直接抛出
                raise

        if last_error:
            raise ModelAPIError(
                f"All models in group '{model_selector}' failed."
            ) from last_error
        raise ModelAPIError(f"No models available for selector '{model_selector}'.")
