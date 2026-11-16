from typing import Optional

import pytest
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

    async def embed(self, documents: list[str]) -> list[list[float]]:
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
    result = store._collection.get(ids=["search"], include=["metadatas"])
    return result["metadatas"][0]


async def test_unchanged_tool_is_not_reindexed(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    tool = _tool()

    await store.sync(_catalog([tool]), embedding)
    await store.sync(_catalog([tool]), embedding)

    assert len(embedding.calls) == 1


async def test_description_change_reindexes_tool(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    await store.sync(_catalog([_tool(description="Search all indexed documents")]), embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


async def test_args_schema_change_reindexes_tool(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    await store.sync(_catalog([_tool(args_schema=ChangedToolArgs)]), embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


async def test_metadata_change_reindexes_and_updates_metadata(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    changed = _tool(metadata={"category": "mcp", "side_effect_class": "read_only"})
    await store.sync(_catalog([changed]), embedding)

    metadata = _stored_metadata(store)
    assert len(embedding.calls) == 2
    assert metadata["definition_hash"] != old_hash
    assert metadata["category"] == "mcp"


def test_metadata_dict_order_does_not_change_hash():
    first = _tool(metadata={"category": "native", "nested": {"a": 1, "b": 2}})
    second = _tool(metadata={"nested": {"b": 2, "a": 1}, "category": "native"})

    assert _tool_definition_hash(first) == _tool_definition_hash(second)


async def test_legacy_row_without_hash_is_reindexed(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    store._collection = store._client.get_or_create_collection("tool_definitions")
    store._collection.upsert(
        ids=["search"],
        documents=["legacy"],
        embeddings=[[1.0, 1.0]],
        metadatas=[{"tool_name": "search", "category": "native"}],
    )
    embedding = FakeEmbeddingClient()
    store = ToolVectorStore(str(tmp_path))

    await store.sync(_catalog([_tool()]), embedding)

    assert len(embedding.calls) == 1
    assert "definition_hash" in _stored_metadata(store)


async def test_removed_tool_is_deleted(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)

    await store.sync(_catalog([]), embedding)

    assert store._collection.get()["ids"] == []


async def test_embedding_model_change_rebuilds_collection(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    first_embedding = FakeEmbeddingClient("model-a")
    await store.sync(_catalog([_tool()]), first_embedding)

    second_embedding = FakeEmbeddingClient("model-b")
    await store.sync(_catalog([_tool()]), second_embedding)

    assert len(second_embedding.calls) == 1
    assert store._collection.metadata["embedding_model"] == "model-b"
    assert "definition_hash" in _stored_metadata(store)


async def test_main_requirement_dict_schema_is_indexed_and_retrieved(tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4

    from application.main_agent import create_main_work_tools

    tools = create_main_work_tools({'account_id': str(uuid4()), 'conversation_id': str(uuid4()),
                                   'trigger_message_id': str(uuid4())}, SimpleNamespace())
    accept = next(tool for tool in tools if tool.name == 'accept_work')
    assert isinstance(accept.args_schema, dict)
    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([accept]), FakeEmbeddingClient())
    result = store._collection.get(ids=['accept_work'], include=['documents'])
    assert result['ids'] == ['accept_work']
    assert 'requirement' in result['documents'][0]


async def test_failed_embedding_preserves_active_index_and_pointer(tmp_path):

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    before = store._collection.name
    pointer = (tmp_path / 'active.json').read_bytes()

    async def fail(documents):
        raise OSError('provider unavailable')

    embedding.embed = fail
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), embedding)
    assert store._collection.name == before
    assert (tmp_path / 'active.json').read_bytes() == pointer
    assert ToolVectorStore(str(tmp_path))._collection.name == before


async def test_incomplete_catalog_cannot_delete_existing_tools(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    await store.sync(_catalog([], complete=False), FakeEmbeddingClient())
    assert store._collection.get()['ids'] == ['search']


async def test_staging_write_failure_preserves_previous_collection(tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    previous = store._collection.name
    def fail(*args, **kwargs):
        raise OSError('write failed')
    monkeypatch.setattr(Collection, 'upsert', fail)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), FakeEmbeddingClient('model-b'))
    assert ToolVectorStore(str(tmp_path))._collection.name == previous
    assert store._collection.get()['ids'] == ['search']


async def test_rebuild_retains_current_and_previous_and_reuses_vectors(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    for index in range(4):
        other = _tool(description=str(index)).model_copy(update={'name': 'other'})
        await store.sync(_catalog([_tool(), other]), embedding)
    assert all(len(batch) == 1 for batch in embedding.calls)
    assert len(store._client.list_collections()) == 2
    assert ToolVectorStore(str(tmp_path))._collection.name == store._collection.name


async def test_borrowed_collection_defers_cleanup_until_restart(tmp_path, caplog):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    borrowed = store.collection
    for index in range(3):
        await store.sync(_catalog([_tool(description=str(index))]), embedding)
    assert borrowed.get()['ids'] == ['search']
    assert len(store._client.list_collections()) > 2
    assert 'deferred' in caplog.text
    restarted = ToolVectorStore(str(tmp_path))
    await restarted.sync(_catalog([_tool(description='2')]), embedding)
    assert len(restarted._client.list_collections()) == 2


async def test_pointer_publication_failure_does_not_switch_index(tmp_path, monkeypatch):
    import os

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    before = store._collection.name
    def fail(*args, **kwargs):
        raise OSError('replace failed')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), FakeEmbeddingClient())
    assert ToolVectorStore(str(tmp_path))._collection.name == before


async def test_query_rejects_vectors_from_a_different_model(tmp_path):

    from sandbox.tools.retriever import ToolRetriever
    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    retriever = ToolRetriever(store, FakeEmbeddingClient('model-b'))
    with pytest.raises(ToolRetrievalError):
        await retriever.retrieve('query', allowed_tool_names=frozenset({'search'}), limit=1)


async def test_embedding_client_propagates_transport_failures(monkeypatch):

    from resources.embedding import EmbeddingClient

    async def fail(*args):
        raise OSError('offline')
    client = EmbeddingClient()
    monkeypatch.setattr(client, '_embed_async', fail)
    with pytest.raises(OSError):
        await client.embed(['search'])


@pytest.mark.parametrize('vectors', [[], [[1.0]], [[0.0, 0.0]], [[float('nan'), 1.0]], [[1.0, 2.0], [2.0, 3.0]]])
async def test_invalid_embedding_never_replaces_active_index(tmp_path, vectors):
    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    before = store._collection.name
    async def invalid(documents):
        return vectors
    embedding.embed = invalid
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), embedding)
    assert store._collection.name == before


def _catalog(tools, *, complete=True):
    from sandbox.tools.registry import ToolDefinitionCatalog
    from sandbox.tools.routing.models import ToolRoutingSpec

    catalog = ToolDefinitionCatalog()
    for tool in tools:
        catalog.add(tool, category=(tool.metadata or {}).get('category', 'native'),
                    routing=ToolRoutingSpec('test.search'))
    if not complete:
        catalog.mark_incomplete('mcp')
    return catalog


async def test_routing_change_reembeds_tool(tmp_path):
    from dataclasses import replace

    from sandbox.tools.registry import ToolDefinitionCatalog
    from sandbox.tools.routing.models import ToolRoutingSpec

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    first = _catalog([_tool()])
    await store.sync(first, embedding)
    second = ToolDefinitionCatalog()
    second.add(_tool(), category='native', routing=replace(ToolRoutingSpec('test.search'), requires_workspace=True))
    await store.sync(second, embedding)
    assert len(embedding.calls) == 2


async def test_endpoint_and_format_changes_rebuild_before_query(tmp_path, monkeypatch):
    from sandbox.tools import retriever as module

    embedding = FakeEmbeddingClient()
    embedding.profile = {'provider': 'local', 'model': embedding.model, 'endpoint': 'http://first/v1'}
    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), embedding)
    initial = store._collection.name
    embedding.profile['endpoint'] = 'http://second/v1'
    await store.sync(_catalog([_tool()]), embedding)
    assert store._collection.name != initial
    second = store._collection.name
    monkeypatch.setattr(module, '_INDEX_FORMAT', 3)
    restarted = ToolVectorStore(str(tmp_path))
    await restarted.sync(_catalog([_tool()]), embedding)
    assert restarted._collection.name != second
    assert len(embedding.calls) == 3
    assert restarted.query(embedding, [[1., 1.]], n_results=1)['ids'] == [['search']]


async def test_restart_falls_back_to_valid_previous_and_rejects_wrong_profile(tmp_path):
    import json

    from sandbox.tools.retriever import ToolRetriever
    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient('model-a'))
    previous = store._collection.name
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient('model-b'))
    store._collection.delete(ids=['search'])
    restarted = ToolVectorStore(str(tmp_path))
    assert restarted._collection.name == previous
    with pytest.raises(ToolRetrievalError):
        await ToolRetriever(restarted, FakeEmbeddingClient('model-b')).retrieve(
            'query', allowed_tool_names=frozenset({'search'}), limit=1)
    assert json.loads((tmp_path / 'active.json').read_text())['previous'] == previous


async def test_failure_after_pointer_replace_restores_old_publication(tmp_path, monkeypatch):
    import os

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    before = (tmp_path / 'active.json').read_bytes()
    original = os.fsync
    calls = 0
    def fail_after_replace(fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('directory fsync failed after replacement')
        return original(fd)
    monkeypatch.setattr(os, 'fsync', fail_after_replace)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='new')]), FakeEmbeddingClient())
    assert (tmp_path / 'active.json').read_bytes() == before
    assert ToolVectorStore(str(tmp_path))._collection.name == store._collection.name


async def test_inflight_query_prevents_cleanup_of_older_collections(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from chromadb.api.models.Collection import Collection

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    oldest = store._collection.name
    entered, release = Event(), Event()
    def block(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {'ids': [['search']]}
    monkeypatch.setattr(Collection, 'query', block)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.query, embedding, [[1., 1.]], n_results=1)
        try:
            assert entered.wait(3)
            for index in range(3):
                await store.sync(_catalog([_tool(description=str(index))]), embedding)
            assert store._client.get_collection(oldest).get()['ids'] == ['search']
            assert len(store._client.list_collections()) == 4
        finally:
            release.set()
        assert future.result()['ids'] == [['search']]
    await store.sync(_catalog([_tool(description='2')]), embedding)
    assert len(store._client.list_collections()) == 2


@pytest.mark.parametrize('vectors', [[], [[1.]], [[0., 0.]], [[1., 2.], [1., 2.]]])
async def test_invalid_query_embedding_is_a_retrieval_error(tmp_path, vectors):
    from sandbox.tools.retriever import ToolRetriever
    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    async def malformed(documents):
        return vectors
    embedding.embed = malformed
    with pytest.raises(ToolRetrievalError):
        await ToolRetriever(store, embedding).retrieve('query', allowed_tool_names=frozenset({'search'}), limit=1)


async def test_empty_catalog_can_later_gain_tools(tmp_path):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([]), embedding)
    await store.sync(_catalog([_tool()]), embedding)
    assert store.query(embedding, [[1., 1.]], n_results=1)['ids'] == [['search']]


async def test_pointer_records_non_secret_model_identity_and_dimension(tmp_path):
    import json

    from resources.embedding import EmbeddingClient

    embedding = FakeEmbeddingClient()
    embedding.profile = EmbeddingClient(base_url='https://user:secret@host:443/v1?key=secret',
                                        model='model-a', provider='local').profile
    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), embedding)
    pointer = json.loads((tmp_path / 'active.json').read_text())
    assert pointer['profile'] == {'provider': 'local', 'model': 'model-a',
                                  'endpoint': 'https://host:443/v1', 'dimension': 2, 'format': 2}
    assert 'secret' not in json.dumps(pointer)


async def test_partial_batch_write_never_mutates_old_index(tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    other = _tool().model_copy(update={'name': 'other'})
    removed = _tool().model_copy(update={'name': 'removed'})
    await store.sync(_catalog([_tool(), other, removed]), embedding)
    before = store._collection.get(include=['metadatas'])
    monkeypatch.setattr(type(store._client), 'get_max_batch_size', lambda self: 1)
    original = Collection.upsert
    writes = 0
    def fail_second(self, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError('partial write')
        return original(self, **kwargs)
    monkeypatch.setattr(Collection, 'upsert', fail_second)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed'), other]), embedding)
    assert store._collection.get(include=['metadatas']) == before
    assert ToolVectorStore(str(tmp_path))._collection.name == store._collection.name


async def test_staging_validation_rejects_corrupted_definition_hash(tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = ToolVectorStore(str(tmp_path))
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    old = store._collection.name
    original = Collection.upsert
    def corrupt(self, **kwargs):
        kwargs['metadatas'][0]['definition_hash'] = 'corrupted'
        return original(self, **kwargs)
    monkeypatch.setattr(Collection, 'upsert', corrupt)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='new')]), FakeEmbeddingClient())
    assert ToolVectorStore(str(tmp_path))._collection.name == old


async def test_cleanup_enumeration_failure_does_not_undo_successful_publication(tmp_path, monkeypatch, caplog):
    store = ToolVectorStore(str(tmp_path))
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    before = store._collection.name
    def unavailable(self, *args, **kwargs):
        raise OSError('collection enumeration unavailable')
    monkeypatch.setattr(type(store._client), 'list_collections', unavailable)
    await store.sync(_catalog([_tool(description='new')]), embedding)
    assert store._collection.name != before
    assert ToolVectorStore(str(tmp_path))._collection.name == store._collection.name
    assert 'deferred' in caplog.text
