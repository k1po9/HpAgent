"""
ToolVectorStore + ToolRetriever —— 基于 ChromaDB 的 RAG 动态工具检索。

工具向量化: name + description + parameter descriptions
持久化路径: tools/vectors/ (ChromaDB)
"""
import logging
from typing import List, Optional

import chromadb
from chromadb.config import Settings
from langchain_core.tools import BaseTool

logger = logging.getLogger("HpAgent.ToolRAG")


class ToolVectorStore:
    """工具向量存储 —— ChromaDB 本地持久化。

    Usage:
        store = ToolVectorStore(persist_path="tools/vectors")
        store.index_tools(tools, embedding_client)
        store.sync(tools)
    """

    def __init__(self, persist_path: str = "tools/vectors"):
        self._client = chromadb.PersistentClient(
            path=persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="tool_definitions",
            metadata={"hnsw:space": "cosine"},
        )

    def index_tools(self, tools: List[BaseTool], embedding_client) -> None:
        docs = []
        ids = []
        metadatas = []
        for tool in tools:
            doc = f"{tool.name}: {tool.description}"
            if tool.args_schema:
                for field_name, field_info in tool.args_schema.model_fields.items():
                    desc = (field_info.description or "") if hasattr(field_info, "description") else ""
                    doc += f" {field_name}: {desc}"

            docs.append(doc)
            ids.append(tool.name)
            metadatas.append({
                "tool_name": tool.name,
                "category": (tool.metadata or {}).get("category", "native") if hasattr(tool, "metadata") else "native",
            })

        if docs:
            embeddings = embedding_client.embed_sync(docs)
            self._collection.upsert(
                ids=ids,
                documents=docs,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            # 记录当前使用的 embedding 模型，供后续换模型时自动失效
            self._set_stored_model(embedding_client.model)

    def sync(self, tools: List[BaseTool], embedding_client=None) -> None:
        """增量同步：删除已移除的工具，新增未索引的工具。

        若检测到 embedding 模型变更，自动清空全部缓存并全量重建。
        """
        import traceback

        try:
            logger.info("sync: start — %d tools, has_embedding=%s", len(tools), embedding_client is not None)
            logger.info("sync: collection=%s id=%s", self._collection, id(self._collection))

            if embedding_client:
                current_model = embedding_client.model
                logger.info("sync: current_model=%s", current_model)
                stored_model = self._get_stored_model()
                logger.info("sync: stored_model=%s", stored_model)
                if stored_model is not None and stored_model != current_model:
                    logger.warning(
                        "Embedding model changed: %s → %s, purging cache and re-indexing all %d tools",
                        stored_model, current_model, len(tools),
                    )
                    self._client.delete_collection("tool_definitions")
                    self._collection = self._client.create_collection(
                        name="tool_definitions",
                        metadata={"hnsw:space": "cosine"},
                    )
                    self.index_tools(tools, embedding_client)
                    logger.info("sync: done (full reindex)")
                    return

            logger.info("sync: calling collection.get()")
            result = self._collection.get()
            logger.info("sync: collection.get() returned type=%s keys=%s", type(result).__name__, list(result.keys()) if result else "None")
            existing_ids = set(result["ids"]) if result and result.get("ids") else set()
            logger.info("sync: existing_ids count=%d", len(existing_ids))
            current_ids = {t.name for t in tools}
            logger.info("sync: current_ids count=%d", len(current_ids))

            to_delete = existing_ids - current_ids
            if to_delete:
                logger.info("sync: deleting %d old tools", len(to_delete))
                self._collection.delete(ids=list(to_delete))

            to_add = [t for t in tools if t.name not in existing_ids]
            logger.info("sync: to_add count=%d", len(to_add))
            if to_add and embedding_client:
                self.index_tools(to_add, embedding_client)
                logger.info("sync: indexed %d new tools", len(to_add))

            logger.info("sync: done (incremental)")
        except Exception:
            logger.error("sync: FAILED — %s", traceback.format_exc())
            raise

    def _get_stored_model(self) -> Optional[str]:
        """读取 ChromaDB collection 中记录的 embedding 模型名。"""
        meta = self._collection.metadata
        if meta is None:
            return None
        return meta.get("embedding_model")

    def _set_stored_model(self, model: str):
        """将当前 embedding 模型名写入 ChromaDB collection metadata。

        注意：ChromaDB 禁止在 modify() 中携带 hnsw:space（即使值未变），
        需先剥离再合并。
        """
        existing = self._collection.metadata or {}
        clean = {k: v for k, v in existing.items() if k != "hnsw:space"}
        clean["embedding_model"] = model
        self._collection.modify(metadata=clean)

    @property
    def collection(self):
        return self._collection


class ToolRetriever:
    """工具语义检索器 —— 基于用户查询检索 Top-K 相关工具。

    支持可选的 Reranker 精排：当提供 reranker_client 时，
    先从 ChromaDB 召回 top_n * 2 候选，再经 reranker 精排后返回 top_k。

    last_scores: 最近一次 retrieve() 的结果评分，{tool_name: relevance_score}。
    由 Sandbox.select_tools() 读取，用于 TOOL_RETRIEVAL 审计事件。
    """

    def __init__(self, vector_store: ToolVectorStore, embedding_client, reranker_client=None):
        self._store = vector_store
        self._embedding = embedding_client
        self._reranker = reranker_client
        self.last_scores: dict[str, float] = {}

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        registry=None,
    ) -> List[BaseTool]:
        fetch_k = top_k * 2 if self._reranker else top_k
        logger.info(
            "retrieve: query=%s top_k=%d fetch_k=%d reranker=%s filter=%s",
            query[:80], top_k, fetch_k, self._reranker is not None, category_filter,
        )

        query_embedding = (await self._embedding.embed([query]))[0]
        logger.info("retrieve: embedding dim=%d", len(query_embedding) if query_embedding else 0)

        where_filter = None
        if category_filter:
            where_filter = {"category": category_filter}

        results = self._store.collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k,
            where=where_filter,
        )

        tool_names = results["ids"][0] if results["ids"] else []
        documents = results.get("documents", [[]])[0] if results.get("documents") else []
        distances = results.get("distances", [[]])[0] if results.get("distances") else []

        logger.info(
            "retrieve: chroma returned %d candidates (fetch_k=%d)",
            len(tool_names), fetch_k,
        )
        # 打印前 5 个候选的名称和 cosine 距离
        for i, (name, dist) in enumerate(zip(tool_names[:5], distances[:5])):
            chroma_sim = round(1.0 - dist, 4) if dist else 0.0
            logger.info("retrieve: chroma[%d] %s dist=%.4f sim=%.4f", i, name, dist, chroma_sim)

        # 构建 ChromaDB 距离 → 相似度映射（cosine distance → 1 - distance）
        chroma_scores: dict[str, float] = {}
        for name, dist in zip(tool_names, distances):
            chroma_scores[name] = round(1.0 - dist, 4) if dist else 0.0

        # Reranker 精排（失败时回退到 ChromaDB 原始分数，不会被 0.0 覆盖）
        if self._reranker and len(tool_names) > top_k:
            logger.info("retrieve: running reranker on %d candidates → top_n=%d", len(tool_names), top_k)
            try:
                rerank_results = await self._reranker.rerank(query, documents, top_n=top_k)
            except Exception:
                logger.warning("Reranker failed, falling back to ChromaDB scores")
                rerank_results = None

            if rerank_results is not None:
                reranked_names = []
                rerank_scores: dict[str, float] = {}
                for rr in rerank_results:
                    if rr.index < len(tool_names):
                        name = tool_names[rr.index]
                        reranked_names.append(name)
                        rerank_scores[name] = round(rr.score, 4)

                # 如果 reranker 最高分 < 0.05，说明模型对这批文档完全无法区分，
                # 退回到 ChromaDB 排序（避免把 reranker 噪声当成有效信号）
                max_rerank = max(rerank_scores.values()) if rerank_scores else 0.0
                if max_rerank < 0.05:
                    logger.info(
                        "retrieve: reranker scores too low (max=%.4f < 0.05), falling back to ChromaDB",
                        max_rerank,
                    )
                    scores = chroma_scores
                else:
                    tool_names = reranked_names
                    scores = {**chroma_scores, **rerank_scores}
                    for i, name in enumerate(tool_names[:5]):
                        logger.info("retrieve: rerank[%d] %s score=%.4f", i, name, scores.get(name, 0.0))
            else:
                scores = chroma_scores
        else:
            scores = chroma_scores

        if registry is None:
            self.last_scores = {}
            return []

        tools = []
        for name in tool_names[:top_k]:  # 最终硬截断：无论上游返回多少，不超过 top_k
            tool = registry.get(name)
            if tool:
                tools.append(tool)

        # 只保留最终入选工具的分数
        self.last_scores = {t.name: scores.get(t.name, 0.0) for t in tools}
        logger.info("retrieve: final %d tools: %s", len(tools), {t.name: round(scores.get(t.name, 0.0), 4) for t in tools})
        return tools

    async def retrieve_for_llm(
        self, query: str, registry, top_k: int = 5
    ) -> List[dict]:
        tools = await self.retrieve(query, top_k=top_k, registry=registry)
        # Use ToolRegistry's shared converter if available, fallback to manual
        convert = getattr(registry, "_tool_to_llm_dict", None)
        if convert:
            return [convert(t) for t in tools]
        result = []
        for t in tools:
            if hasattr(t, "to_openai_function"):
                result.append(t.to_openai_function())
            else:
                result.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.args_schema.model_json_schema() if t.args_schema else {},
                    },
                })
        return result
