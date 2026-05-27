"""
Resources —— 外部资源访问层（模型凭据、API 密钥管理）。

三个核心类:
  - CredentialManager: API 密钥的加密存储 + 模型退避链配置
  - ModelClient:      单个模型 API 的 HTTP 客户端（支持流式和非流式调用）
  - ResourcePool:     多模型注册 + 退避链调度
"""
from .resource_pool import ResourcePool
from .credentials import CredentialManager, ModelEndpoint
from .model_client import ModelClient

__all__ = ["ResourcePool", "CredentialManager", "ModelEndpoint", "ModelClient"]
