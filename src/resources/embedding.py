"""
轻量级 embedding 客户端 —— 调用 OpenAI 兼容的 /v1/embeddings 端点。

支持任意 OpenAI 兼容的 embedding 服务:
  - HuggingFace TEI (Text Embeddings Inference)
  - OpenAI / Azure OpenAI
  - 其他兼容服务

Usage:
    client = EmbeddingClient(
        base_url="http://embeddings:8080/v1",
        api_key="",
        model="BAAI/bge-m3",
    )
    # Async context:
    vectors = await client.embed(["calculator: evaluate math expression"])
    # Sync context (no event loop running):
    vectors = client.embed_sync(["calculator: evaluate math expression"])
"""
import asyncio
import logging
import math
from typing import List
from urllib.parse import urlsplit

logger = logging.getLogger("HpAgent.Embedding")


class EmbeddingClient:
    """轻量级 embedding 客户端 —— OpenAI 兼容协议。

    支持 TEI / OpenAI / Azure / 其他兼容服务。
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "BAAI/bge-m3",
        timeout: float = 5.0,
        provider: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._provider = provider

    @property
    def model(self) -> str:
        """当前使用的 embedding 模型名称，用于向量缓存版本追踪。"""
        return self._model

    # ── 公开接口 ──────────────────────────────────────────────────

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """向量化文本列表（async —— 在异步上下文中使用）。"""
        vectors = await self._embed_async(texts)
        validate_embeddings(vectors, len(texts))
        return vectors

    @property
    def profile(self) -> dict:
        """Configured identity without credentials, query strings or fragments."""
        endpoint = urlsplit(self._base_url)
        return {"provider": self._provider, "model": self.model,
                "endpoint": f"{endpoint.scheme}://{endpoint.hostname or ''}:{endpoint.port or ''}{endpoint.path}"}

    def embed_sync(self, texts: List[str]) -> List[List[float]]:
        """Compatibility for synchronous consumers; tool indexing awaits embed()."""
        import concurrent.futures

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.embed(texts))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self.embed(texts))
            return future.result(timeout=self._timeout)

    # ── 内部实现 ──────────────────────────────────────────────────

    async def _embed_async(self, texts: List[str]) -> List[List[float]]:
        """调用 OpenAI 兼容的 /v1/embeddings 端点。"""
        import httpx

        url = f"{self._base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        body = {
            "model": self._model,
            "input": texts,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                if [item.get("index") for item in items] != list(range(len(texts))):
                    raise ValueError("embedding response indices/count mismatch")
                return [item["embedding"] for item in items]
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Embedding API error status=%s",
                e.response.status_code,
            )
            raise


def create_embedding_client(models_config) -> EmbeddingClient:
    """从 ModelsConfig 创建 EmbeddingClient。"""
    import os
    import re

    embedding_chain = models_config.embedding
    if not embedding_chain:
        raise ValueError("tool retrieval requires an embedding model")

    entry = embedding_chain[0]
    provider_name = entry.provider
    model = entry.model
    timeout = getattr(entry, "timeout", 5.0) or 5.0

    providers = models_config.providers
    provider_cfg = providers.get(provider_name, {})
    base_url = provider_cfg.base_url if hasattr(provider_cfg, "base_url") else ""
    api_key = getattr(provider_cfg, "api_key", "") or ""

    api_key = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), api_key)

    logger.info(
        "EmbeddingClient created: provider=%s model=%s",
        provider_name, model,
    )
    return EmbeddingClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        provider=provider_name,
    )


def validate_embeddings(vectors, count: int, dimension: int | None = None) -> int:
    """Reject missing, malformed, non-finite and placeholder zero vectors."""
    if len(vectors) != count:
        raise ValueError("embedding count mismatch")
    for vector in vectors:
        if dimension is None:
            dimension = len(vector)
        if not dimension or len(vector) != dimension:
            raise ValueError("embedding dimension mismatch")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in vector) or not any(vector):
            raise ValueError("invalid embedding values")
    return dimension or 0
