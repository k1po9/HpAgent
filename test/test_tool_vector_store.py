from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from sandbox.tools.retriever import ToolVectorStore, _tool_definition_hash


class ToolArgs(BaseModel):
    query: str = Field(description="Search query")
    limit: int = Field(default=10, description="Maximum results")


class ChangedToolArgs(BaseModel):
    query: str = Field(description="Search query")
    limit: int = Field(default=20, description="Maximum results")


class FakeEmbeddingClient:
    def __init__(self, model: str = "model-a"):
        self.model = model
        self.calls: list[list[str]] = []

    def embed_sync(self, documents: list[str]) -> list[list[float]]:
        self.calls.append(documents)
        return [[float(len(document)), 1.0] for document in documents]


def _tool(
    *,
    description: str = "Search documents",
    args_schema: type[BaseModel] = ToolArgs,
    metadata: Optional[dict] = None,
):
    async def search(**_kwargs):
        return "ok"

    return StructuredTool.from_function(
        name="search",
        description=description,
        args_schema=args_schema,
        coroutine=search,
        metadata=metadata or {"category": "native", "side_effect_class": "read_only"},
    )


def _stored_metadata(store: ToolVectorStore) -> dict:
    result = store.collection.get(ids=["search"], include=["metadatas"])
    return result["metadatas"][0]


def test_unchanged_tool_is_not_reindexed(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    tool = _tool()

    store.sync([tool], embedding)
    store.sync([tool], embedding)

    assert len(embedding.calls) == 1


def test_description_change_reindexes_tool(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    store.sync([_tool()], embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    store.sync([_tool(description="Search all indexed documents")], embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


def test_args_schema_change_reindexes_tool(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    store.sync([_tool()], embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    store.sync([_tool(args_schema=ChangedToolArgs)], embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


def test_metadata_change_reindexes_and_updates_metadata(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    store.sync([_tool()], embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    changed = _tool(metadata={"category": "mcp", "side_effect_class": "read_only"})
    store.sync([changed], embedding)

    metadata = _stored_metadata(store)
    assert len(embedding.calls) == 2
    assert metadata["definition_hash"] != old_hash
    assert metadata["category"] == "mcp"


def test_metadata_dict_order_does_not_change_hash():
    first = _tool(metadata={"category": "native", "nested": {"a": 1, "b": 2}})
    second = _tool(metadata={"nested": {"b": 2, "a": 1}, "category": "native"})

    assert _tool_definition_hash(first) == _tool_definition_hash(second)


def test_legacy_row_without_hash_is_reindexed(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    store.collection.upsert(
        ids=["search"],
        documents=["legacy"],
        embeddings=[[1.0, 1.0]],
        metadatas=[{"tool_name": "search", "category": "native"}],
    )
    embedding = FakeEmbeddingClient()

    store.sync([_tool()], embedding)

    assert len(embedding.calls) == 1
    assert "definition_hash" in _stored_metadata(store)


def test_removed_tool_is_deleted(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    store.sync([_tool()], embedding)

    store.sync([], embedding)

    assert store.collection.get()["ids"] == []


def test_embedding_model_change_rebuilds_collection(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    first_embedding = FakeEmbeddingClient("model-a")
    store.sync([_tool()], first_embedding)

    second_embedding = FakeEmbeddingClient("model-b")
    store.sync([_tool()], second_embedding)

    assert len(second_embedding.calls) == 1
    assert store.collection.metadata["embedding_model"] == "model-b"
    assert "definition_hash" in _stored_metadata(store)
