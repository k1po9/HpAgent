from typing import Dict, Any, Optional, List
import httpx
import json
from ..common.interfaces import IResources
from ..common.errors import ModelAPIError, ValidationError


class ResourcePool(IResources):
    def __init__(self, credential_manager=None):
        self._credential_manager = credential_manager
        self._model_clients: Dict[str, Any] = {}
        self._storage_path: Optional[str] = None

    def set_credential_manager(self, credential_manager):
        self._credential_manager = credential_manager

    def set_storage_path(self, path: str):
        self._storage_path = path

    async def get_model_client(self, model_name: str, config: Dict[str, Any]) -> Any:
        client_key = f"{model_name}:{json.dumps(config, sort_keys=True)}"
        if client_key in self._model_clients:
            return self._model_clients[client_key]
        from ..model.client import ModelClient
        client = ModelClient(config=config)
        self._model_clients[client_key] = client
        return client

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
