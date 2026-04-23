from typing import Dict, Any, Optional, List
import httpx
import json
from ..common.interfaces import IResources
from ..common.errors import ModelAPIError, ValidationError


class ResourcePool(IResources):
    def __init__(self, credential_manager=None):
        self._credential_manager = credential_manager
        self._model_clients: Dict[str, Any] = {}
        self._fallback_groups: Dict[str, List[str]] = {}
        self._storage_path: Optional[str] = None

    def set_credential_manager(self, credential_manager):
        self._credential_manager = credential_manager

    def set_storage_path(self, path: str):
        self._storage_path = path

    async def initialize_models(self) -> None:
        if not self._credential_manager:
            return
        endpoints = self._credential_manager.get_model_endpoint_list()
        if not endpoints:
            return

        from ..model.client import ModelClient
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

        if client_ids:
            self._fallback_groups["default"] = client_ids

    async def get_credential(self, resource_id: str, scope: List[str]) -> str:
        if not self._credential_manager:
            raise ValidationError("credential_manager", "Not initialized")
        return self._credential_manager.issue_temp_token(resource_id, scope)

    async def proxy_request(self, target_url: str, method: str, resource_id: str, headers: Optional[Dict[str, str]] = None, body: Optional[Any] = None) -> Dict[str, Any]:
        if not self._credential_manager:
            raise ValidationError("credential_manager", "Not initialized")
        token_data = None
        if headers and "Authorization" in headers:
            token_id = headers["Authorization"].replace("Bearer ", "")
            token_data = self._credential_manager.validate_token(token_id)
        if not token_data:
            raise ValidationError("authorization", "Invalid or expired token")
        request_headers = headers or {}
        request_headers.pop("Authorization", None)
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
                return {"status_code": response.status_code, "body": response.json() if response.text else None, "headers": dict(response.headers)}
            except httpx.HTTPStatusError as e:
                raise ModelAPIError(reason=str(e), status_code=e.response.status_code)
            except Exception as e:
                raise ModelAPIError(reason=str(e))

    # =============================== Model Management ===========================
    async def register_model(self, model_id: str, client: Any, priority: int = 0) -> None:
        self._model_clients[model_id] = {"client": client, "priority": priority}

    async def configure_fallback(self, group_name: str, primary: str, *fallbacks: str) -> None:
        self._fallback_groups[group_name] = [primary] + list(fallbacks)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model_selector: str = "default",        
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ) -> Any:
        candidate_ids = self._fallback_groups.get(model_selector, [model_selector])
        last_error = None

        for model_id in candidate_ids:
            model_info = self._model_clients.get(model_id)
            if not model_info:
                continue
            client = model_info["client"]
            try:
                return await client.generate(messages=messages, tools=tools, stream=stream)
            except (ModelAPIError, ConnectionError, TimeoutError) as e:
                last_error = e
                continue
            except Exception:
                raise

        if last_error:
            raise ModelAPIError(f"All models in group '{model_selector}' failed.") from last_error
        raise ModelAPIError(f"No models available for selector '{model_selector}'.")