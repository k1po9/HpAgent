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
    vectors = client.embed_texts(["calculator: evaluate math expression"])
"""
import logging
from typing import List

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
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    # ── 公开接口 ──────────────────────────────────────────────────

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """同步方式向量化文本列表。"""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                future = concurrent.futures.Future()

                async def _run():
                    try:
                        result = await self._embed_async(texts)
                        future.set_result(result)
                    except Exception as e:
                        future.set_exception(e)

                loop.create_task(_run())
                return future.result(timeout=self._timeout)
            else:
                return loop.run_until_complete(self._embed_async(texts))
        except Exception as e:
            logger.warning("Embedding failed: %s, using zero vectors", e)
            return [[0.0] * 1024 for _ in texts]

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
                # OpenAI 格式: {"data": [{"embedding": [...], "index": 0}, ...]}
                items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in items]
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Embedding API error %s: %s",
                e.response.status_code,
                e.response.text[:200],
            )
            raise


def create_embedding_client(models_config) -> EmbeddingClient:
    """从 ModelsConfig 创建 EmbeddingClient。"""
    import os
    import re

    embedding_chain = models_config.embedding
    if not embedding_chain:
        logger.warning("No embedding model configured, using zero vectors fallback")
        return EmbeddingClient()

    entry = embedding_chain[0]
    provider_name = entry.provider
    model = entry.model
    timeout = getattr(entry, "timeout", 30.0) or 30.0

    providers = models_config.providers
    provider_cfg = providers.get(provider_name, {})
    base_url = provider_cfg.base_url if hasattr(provider_cfg, "base_url") else ""
    api_key = getattr(provider_cfg, "api_key", "") or ""

    api_key = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), api_key)

    logger.info(
        "EmbeddingClient created: provider=%s model=%s base_url=%s",
        provider_name, model, base_url,
    )
    return EmbeddingClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
