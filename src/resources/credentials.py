"""
CredentialManager —— API 密钥安全管理和模型退避链配置。

安全设计:
  - API 密钥通过 _encrypt / _decrypt 存储（当前为明文占位，生产应替换为 KMS / 环境变量）
  - ModelEndpoint 列表中的 api_key 被剥离（存储时置空），通过 get_model_endpoint_list() 解密后返回
  - 临时 token 机制：issue_temp_token() 签发有时效范围令牌，validate_token() 校验

模型退避链:
  - register_model_chain([ep1, ep2, ep3]) → 注册有序端点列表
  - ResourcePool.generate() 时按列表顺序逐一尝试，失败自动跳到下一个
"""
from typing import Dict, Any, Optional, List
from threading import RLock
from dataclasses import dataclass, field
import time
import uuid


@dataclass
class Credential:
    """单个凭据实体 —— 存储加密后的 API 密钥及元信息。

    Attributes:
        resource_id: 资源标识（如 "model_endpoint:0:anthropic"）。
        credential_type: 凭据类型（"api_key" / "oauth_token" 等）。
        encrypted_value: 加密后的凭据值。
        scope: 权限范围列表（如 ["model:invoke"]）。
        created_at / expires_at: 创建和过期时间戳。
        metadata: 附加元数据（provider / base_url / model 等）。
    """
    resource_id: str
    credential_type: str
    encrypted_value: str
    scope: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """检查凭据是否过期（expires_at 为 None 表示永不过期）。"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def has_scope(self, required_scope: str) -> bool:
        """检查凭据是否包含指定权限（"all" 表示万能权限）。"""
        if "all" in self.scope:
            return True
        return required_scope in self.scope


@dataclass
class ModelEndpoint:
    """模型端点元数据 —— 一个 LLM 服务地址的配置信息。

    Attributes:
        provider: 提供商（"openai" / "anthropic" / "azure"）。
        api_key: API 密钥（存储时会被剥离，使用时通过 CredentialManager 解密填充）。
        base_url: API 基础 URL。
        model: 模型名称（如 "claude-sonnet-4-6"）。
        extra: 额外配置（如自定义 headers）。
    """
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class CredentialManager:
    """凭据管理器 —— API 密钥的加密存储 + 临时 token + 模型端点列表管理。

    线程安全: 所有写操作通过 RLock 保护。
    """

    def __init__(self):
        self._credentials: Dict[str, Credential] = {}          # resource_id → Credential
        self._temp_tokens: Dict[str, Dict[str, Any]] = {}      # token_id → token_data
        self._model_endpoints: List[ModelEndpoint] = []        # 模型端点列表（不含明文密钥）
        self._lock = RLock()

    def register_model_chain(self, endpoint_list: List[ModelEndpoint]) -> None:
        """注册一个有序模型端点列表，用于退化访问。

        处理流程:
          1. 清空旧端点配置。
          2. 清除之前关联的模型凭据。
          3. 遍历新端点列表 → 每个端点的 api_key 加密存储 → 列表保存脱敏副本。
          4. resource_id 格式: f"model_endpoint:{index}:{provider}"。

        Args:
            endpoint_list: 按优先级降序排列的端点列表（第一个是首选）。
        """
        with self._lock:
            self._model_endpoints = []
            # 清除旧凭据
            keys_to_remove = [
                rid for rid in self._credentials.keys()
                if rid.startswith("model_endpoint:")
            ]
            for key in keys_to_remove:
                del self._credentials[key]

            for idx, endpoint in enumerate(endpoint_list):
                resource_id = f"model_endpoint:{idx}:{endpoint.provider}"
                # API Key 加密存储
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
                        **endpoint.extra,
                    },
                )
                # 列表存储脱敏副本（api_key 置空）
                sanitized_endpoint = ModelEndpoint(
                    provider=endpoint.provider,
                    api_key="",  # 不再明文存储
                    base_url=endpoint.base_url,
                    model=endpoint.model,
                    extra=endpoint.extra,
                )
                self._model_endpoints.append(sanitized_endpoint)

    def get_model_endpoint_list(self) -> List[ModelEndpoint]:
        """返回完整的模型端点列表（API Key 已解密填充）。

        保持注册时的顺序，供 ResourcePool 按退避链尝试。
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
                    extra=endpoint.extra,
                ))
            return result

    def register_credential(
        self,
        resource_id: str,
        credential_type: str,
        value: str,
        scope: Optional[list] = None,
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """注册一个凭据（加密存储）。

        Args:
            resource_id: 资源标识。
            credential_type: 凭据类型。
            value: 凭据明文值（内部加密后存储）。
            scope: 权限范围列表。
            expires_at: 过期时间戳（None 表示永不过期）。
            metadata: 附加元数据。
        """
        with self._lock:
            credential = Credential(
                resource_id=resource_id,
                credential_type=credential_type,
                encrypted_value=self._encrypt(value),
                scope=scope or [],
                expires_at=expires_at,
                metadata=metadata or {},
            )
            self._credentials[resource_id] = credential

    def get_credential(self, resource_id: str) -> Optional[Credential]:
        """按 resource_id 查询凭据（不解密）。"""
        with self._lock:
            return self._credentials.get(resource_id)

    def issue_temp_token(
        self, resource_id: str, scope: list, ttl_seconds: int = 3600
    ) -> str:
        """签发有时效的临时访问 token。

        校验凭据存在、未过期、且包含所需 scope 后，
        生成 UUID 作为 token_id，存储 token 元信息到 _temp_tokens。

        Args:
            resource_id: 目标资源标识。
            scope: 请求的权限范围。
            ttl_seconds: token 有效期（秒），默认 3600（1 小时）。

        Returns:
            token_id 字符串。

        Raises:
            ValueError: 凭据不存在、已过期、或 scope 不匹配。
        """
        with self._lock:
            credential = self._credentials.get(resource_id)
            if not credential:
                raise ValueError(f"Credential not found: {resource_id}")
            if credential.is_expired():
                raise ValueError(f"Credential expired: {resource_id}")
            for required_scope in scope:
                if not credential.has_scope(required_scope):
                    raise ValueError(
                        f"Scope '{required_scope}' not allowed for resource '{resource_id}'"
                    )
            token_id = str(uuid.uuid4())
            self._temp_tokens[token_id] = {
                "resource_id": resource_id,
                "scope": scope,
                "issued_at": time.time(),
                "expires_at": time.time() + ttl_seconds,
            }
            return token_id

    def validate_token(self, token_id: str) -> Optional[Dict[str, Any]]:
        """验证临时 token。

        Returns:
            token 元数据字典（含 resource_id / scope / expires_at），
            token 不存在或已过期返回 None。
        """
        with self._lock:
            token_data = self._temp_tokens.get(token_id)
            if not token_data:
                return None
            if time.time() > token_data["expires_at"]:
                del self._temp_tokens[token_id]
                return None
            return token_data

    def revoke_token(self, token_id: str) -> bool:
        """撤销临时 token。"""
        with self._lock:
            if token_id in self._temp_tokens:
                del self._temp_tokens[token_id]
                return True
            return False

    # ── 加密/解密（占位实现 —— 生产环境应使用 KMS / 环境变量） ──

    def _encrypt(self, value: str) -> str:
        """加密凭据值（当前为明文占位）。"""
        return value

    def _decrypt(self, encrypted_value: str) -> str:
        """解密凭据值（当前为明文占位）。"""
        return encrypted_value

    def get_decrypted_credential(self, resource_id: str) -> Optional[str]:
        """获取解密后的凭据明文值。"""
        credential = self.get_credential(resource_id)
        if not credential:
            return None
        return self._decrypt(credential.encrypted_value)
