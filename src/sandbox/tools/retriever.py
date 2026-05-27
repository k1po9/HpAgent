"""
ToolVectorStore + ToolRetriever —— 基于 ChromaDB 的 RAG 动态工具检索。

工具向量化: name + description + parameter descriptions
持久化路径: tools/vectors/ (ChromaDB)
"""
from typing import List, Optional

import chromadb
from chromadb.config import Settings
from langchain_core.tools import BaseTool


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
                "category": getattr(tool, "category", "native") if hasattr(tool, "category") else "native",
            })

        if docs:
            embeddings = embedding_client.embed_texts(docs)
            self._collection.upsert(
                ids=ids,
                documents=docs,
                embeddings=embeddings,
                metadatas=metadatas,
            )

    def sync(self, tools: List[BaseTool], embedding_client=None) -> None:
        """增量同步：删除已移除的工具，新增未索引的工具。"""
        existing_ids = set(self._collection.get()["ids"])
        current_ids = {t.name for t in tools}

        to_delete = existing_ids - current_ids
        if to_delete:
            self._collection.delete(ids=list(to_delete))

        to_add = [t for t in tools if t.name not in existing_ids]
        if to_add and embedding_client:
            self.index_tools(to_add, embedding_client)

    @property
    def collection(self):
        return self._collection


class ToolRetriever:
    """工具语义检索器 —— 基于用户查询检索 Top-K 相关工具。"""

    def __init__(self, vector_store: ToolVectorStore, embedding_client):
        self._store = vector_store
        self._embedding = embedding_client

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        registry=None,
    ) -> List[BaseTool]:
        query_embedding = self._embedding.embed_texts([query])[0]

        where_filter = None
        if category_filter:
            where_filter = {"category": category_filter}

        results = self._store.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
        )

        tool_names = results["ids"][0] if results["ids"] else []
        if registry is None:
            return []

        tools = []
        for name in tool_names:
            tool = registry.get(name)
            if tool:
                tools.append(tool)
        return tools

    async def retrieve_for_llm(
        self, query: str, registry, top_k: int = 5
    ) -> List[dict]:
        tools = await self.retrieve(query, top_k=top_k, registry=registry)
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
