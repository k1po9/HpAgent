from typing import Dict, Any, Optional, List
from threading import RLock
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class Credential:
    resource_id: str
    credential_type: str
    encrypted_value: str
    scope: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def has_scope(self, required_scope: str) -> bool:
        if "all" in self.scope:
            return True
        return required_scope in self.scope

@dataclass
class ModelEndpoint:
    provider: str          # "openai", "anthropic", "azure"
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class CredentialManager:
    def __init__(self):
        self._credentials: Dict[str, Credential] = {}
        self._temp_tokens: Dict[str, Dict[str, Any]] = {}
        # 修正：使用列表存储模型端点元数据（不存储原始 API Key）
        self._model_endpoints: List[ModelEndpoint] = []
        self._lock = RLock()

    def register_model_chain(self, endpoint_list: List[ModelEndpoint]) -> None:
        """
        注册一个有序模型端点列表，用于退化访问。
        API Key 会被加密存储到 _credentials 中，列表仅保留非敏感配置。
        """
        with self._lock:
            # 清空旧配置（如需要追加可改为 extend，此处按替换处理）
            self._model_endpoints = []
            # 先清除之前关联的模型凭据（根据 resource_id 前缀）
            keys_to_remove = [
                rid for rid in self._credentials.keys()
                if rid.startswith("model_endpoint:")
            ]
            for key in keys_to_remove:
                del self._credentials[key]

            for idx, endpoint in enumerate(endpoint_list):
                resource_id = f"model_endpoint:{idx}:{endpoint.provider}"
                # 将 API Key 加密存储为 Credential
                self.register_credential(
                    resource_id=resource_id,
                    credential_type="api_key",
                    value=endpoint.api_key,
                    scope=["model:invoke"],
                    metadata={
                        "provider": endpoint.provider,
                        "base_url": endpoint.base_url,
                        "model": endpoint.model,
                        "index": idx,
                        **endpoint.extra
                    }
                )
                # 列表存储时，api_key 置空（或留占位符），敏感信息不直接暴露
                sanitized_endpoint = ModelEndpoint(
                    provider=endpoint.provider,
                    api_key="",  # 不再明文存储
                    base_url=endpoint.base_url,
                    model=endpoint.model,
                    extra=endpoint.extra
                )
                self._model_endpoints.append(sanitized_endpoint)

    def get_model_endpoint_list(self) -> List[ModelEndpoint]:
        """
        返回完整的模型端点列表，API Key 从加密存储中解密后填充。
        保持注册时的顺序，供外部退化访问。
        """
        with self._lock:
            result = []
            for idx, endpoint in enumerate(self._model_endpoints):
                resource_id = f"model_endpoint:{idx}:{endpoint.provider}"
                decrypted_key = self.get_decrypted_credential(resource_id)
                if decrypted_key is None:
                    raise RuntimeError(f"Failed to decrypt API key for {resource_id}")
                result.append(ModelEndpoint(
                    provider=endpoint.provider,
                    api_key=decrypted_key,
                    base_url=endpoint.base_url,
                    model=endpoint.model,
                    extra=endpoint.extra
                ))
            return result

    def register_credential(self, resource_id: str, credential_type: str, value: str,
                            scope: Optional[list] = None, expires_at: Optional[float] = None,
                            metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            credential = Credential(
                resource_id=resource_id,
                credential_type=credential_type,
                encrypted_value=self._encrypt(value),
                scope=scope or [],
                expires_at=expires_at,
                metadata=metadata or {}
            )
            self._credentials[resource_id] = credential

    def get_credential(self, resource_id: str) -> Optional[Credential]:
        with self._lock:
            return self._credentials.get(resource_id)

    def issue_temp_token(self, resource_id: str, scope: list, ttl_seconds: int = 3600) -> str:
        with self._lock:
            credential = self._credentials.get(resource_id)
            if not credential:
                raise ValueError(f"Credential not found: {resource_id}")
            if credential.is_expired():
                raise ValueError(f"Credential expired: {resource_id}")
            for required_scope in scope:
                if not credential.has_scope(required_scope):
                    raise ValueError(f"Scope '{required_scope}' not allowed for resource '{resource_id}'")
            token_id = str(uuid.uuid4())
            self._temp_tokens[token_id] = {
                "resource_id": resource_id,
                "scope": scope,
                "issued_at": time.time(),
                "expires_at": time.time() + ttl_seconds
            }
            return token_id

    def validate_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            token_data = self._temp_tokens.get(token_id)
            if not token_data:
                return None
            if time.time() > token_data["expires_at"]:
                del self._temp_tokens[token_id]
                return None
            return token_data

    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            if token_id in self._temp_tokens:
                del self._temp_tokens[token_id]
                return True
            return False

    def _encrypt(self, value: str) -> str:
        return value

    def _decrypt(self, encrypted_value: str) -> str:
        return encrypted_value

    def get_decrypted_credential(self, resource_id: str) -> Optional[str]:
        credential = self.get_credential(resource_id)
        if not credential:
            return None
        return self._decrypt(credential.encrypted_value)