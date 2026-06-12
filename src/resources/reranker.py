"""
Reranker 客户端 —— 调用 SiliconFlow /rerank API。

SiliconFlow 的 rerank 端点是自定义 API（非 OpenAI 兼容），
需要专用的 HTTP 客户端。

Usage:
    client = RerankerClient(
        base_url="https://api.siliconflow.cn/v1",
        api_key="sk-xxx",
        model="BAAI/bge-reranker-v2-m3",
    )
    # Async:
    results = await client.rerank("查询", ["文档1", "文档2"])
    # Sync:
    results = client.rerank_sync("查询", ["文档1", "文档2"])
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger("HpAgent.Reranker")


@dataclass
class RerankResult:
    """单条重排序结果。"""
    index: int
    score: float
    text: str = ""


class RerankerClient:
    """SiliconFlow Rerank API 客户端。"""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "BAAI/bge-reranker-v2-m3",
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    # ── 公开接口 ──────────────────────────────────────────────────

    async def rerank(
        self, query: str, documents: List[str], top_n: int = 10
    ) -> List[RerankResult]:
        """对候选文档重排序（async —— 在异步上下文中使用）。

        失败时抛出异常，由调用方决定回退策略（保留 ChromaDB 原始分数）。
        """
        return await self._rerank_async(query, documents, top_n)

    def rerank_sync(
        self, query: str, documents: List[str], top_n: int = 10
    ) -> List[RerankResult]:
        """对候选文档重排序（sync —— 仅在无事件循环的同步上下文中使用）。

        失败时抛出异常，由调用方决定回退策略。
        """
        import concurrent.futures

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self._rerank_async(query, documents, top_n))

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run, self._rerank_async(query, documents, top_n)
                )
                return future.result(timeout=self._timeout)
        except Exception as e:
            logger.warning("Rerank failed: %s, falling back to ChromaDB scores", e)
            raise

    # ── 内部实现 ──────────────────────────────────────────────────

    async def _rerank_async(
        self, query: str, documents: List[str], top_n: int
    ) -> List[RerankResult]:
        """调用 SiliconFlow /rerank 端点。"""
        import httpx

        url = f"{self._base_url}/rerank"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append(RerankResult(
                index=item.get("index", 0),
                score=item.get("relevance_score", 0.0),
                text=item.get("document", {}).get("text", "") if isinstance(item.get("document"), dict) else "",
            ))
        return results


def create_reranker_client(models_config) -> RerankerClient | None:
    """从 ModelsConfig 创建 RerankerClient。未配置 rerank 时返回 None。"""
    import os
    import re

    rerank_cfg = getattr(models_config, "rerank", None)
    if rerank_cfg is None or not rerank_cfg.provider:
        logger.info("No rerank provider configured, reranker disabled")
        return None

    provider_name = rerank_cfg.provider
    model = rerank_cfg.model
    timeout = rerank_cfg.timeout

    providers = models_config.providers
    provider_cfg = providers.get(provider_name)
    if provider_cfg is None:
        logger.warning("Rerank provider '%s' not found in providers", provider_name)
        return None

    base_url = provider_cfg.base_url if hasattr(provider_cfg, "base_url") else ""
    api_key = getattr(provider_cfg, "api_key", "") or ""
    api_key = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ""), api_key)

    logger.info(
        "RerankerClient created: provider=%s model=%s base_url=%s",
        provider_name, model, base_url,
    )
    return RerankerClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )
