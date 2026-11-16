import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from threading import Event, Thread, get_ident
from typing import Optional

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from sandbox.tools.retriever import ToolRetriever, ToolVectorStore, _tool_definition_hash
from sandbox.tools.routing.models import ToolRetrievalError


@pytest.fixture
async def store_factory():
    async with AsyncExitStack() as stack:
        async def open_store(path):
            return await stack.enter_async_context(ToolVectorStore(str(path)))
        yield open_store


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


async def test_unchanged_tool_is_not_reindexed(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    tool = _tool()

    await store.sync(_catalog([tool]), embedding)
    await store.sync(_catalog([tool]), embedding)

    assert len(embedding.calls) == 1


async def test_description_change_reindexes_tool(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    await store.sync(_catalog([_tool(description="Search all indexed documents")]), embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


async def test_args_schema_change_reindexes_tool(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old_hash = _stored_metadata(store)["definition_hash"]

    await store.sync(_catalog([_tool(args_schema=ChangedToolArgs)]), embedding)

    assert len(embedding.calls) == 2
    assert _stored_metadata(store)["definition_hash"] != old_hash


async def test_metadata_change_reindexes_and_updates_metadata(store_factory, tmp_path):
    store = await store_factory(tmp_path)
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


async def test_legacy_row_without_hash_is_reindexed(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    store._collection = store._client.get_or_create_collection("tool_definitions")
    store._collection.upsert(
        ids=["search"],
        documents=["legacy"],
        embeddings=[[1.0, 1.0]],
        metadatas=[{"tool_name": "search", "category": "native"}],
    )
    embedding = FakeEmbeddingClient()
    store = await store_factory(tmp_path)

    await store.sync(_catalog([_tool()]), embedding)

    assert len(embedding.calls) == 1
    assert "definition_hash" in _stored_metadata(store)


async def test_removed_tool_is_deleted(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)

    await store.sync(_catalog([]), embedding)

    assert store._collection.get()["ids"] == []


async def test_embedding_model_change_rebuilds_collection(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    first_embedding = FakeEmbeddingClient("model-a")
    await store.sync(_catalog([_tool()]), first_embedding)

    second_embedding = FakeEmbeddingClient("model-b")
    await store.sync(_catalog([_tool()]), second_embedding)

    assert len(second_embedding.calls) == 1
    assert store._collection.metadata["embedding_model"] == "model-b"
    assert "definition_hash" in _stored_metadata(store)


async def test_main_requirement_dict_schema_is_indexed_and_retrieved(store_factory, tmp_path):
    from types import SimpleNamespace
    from uuid import uuid4

    from application.main_agent import create_main_work_tools

    tools = create_main_work_tools({'account_id': str(uuid4()), 'conversation_id': str(uuid4()),
                                   'trigger_message_id': str(uuid4())}, SimpleNamespace())
    accept = next(tool for tool in tools if tool.name == 'accept_work')
    assert isinstance(accept.args_schema, dict)
    store = await store_factory(tmp_path)
    await store.sync(_catalog([accept]), FakeEmbeddingClient())
    result = store._collection.get(ids=['accept_work'], include=['documents'])
    assert result['ids'] == ['accept_work']
    assert 'requirement' in result['documents'][0]


async def test_failed_embedding_preserves_active_index_and_pointer(store_factory, tmp_path):

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
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
    assert (await store_factory(tmp_path))._collection.name == before


async def test_incomplete_catalog_cannot_delete_existing_tools(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    await store.sync(_catalog([], complete=False), FakeEmbeddingClient())
    assert store._collection.get()['ids'] == ['search']


async def test_staging_write_failure_preserves_previous_collection(store_factory, tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    previous = store._collection.name
    def fail(*args, **kwargs):
        raise OSError('write failed')
    monkeypatch.setattr(Collection, 'upsert', fail)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), FakeEmbeddingClient('model-b'))
    assert (await store_factory(tmp_path))._collection.name == previous
    assert store._collection.get()['ids'] == ['search']


async def test_rebuild_retains_current_and_previous_and_reuses_vectors(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    for index in range(4):
        other = _tool(description=str(index)).model_copy(update={'name': 'other'})
        await store.sync(_catalog([_tool(), other]), embedding)
    assert all(len(batch) == 1 for batch in embedding.calls)
    assert len(store._client.list_collections()) == 2
    assert (await store_factory(tmp_path))._collection.name == store._collection.name


async def test_borrowed_collection_defers_cleanup_until_restart(store_factory, tmp_path, caplog):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    borrowed = store.collection
    for index in range(3):
        await store.sync(_catalog([_tool(description=str(index))]), embedding)
    assert borrowed.get()['ids'] == ['search']
    assert len(store._client.list_collections()) > 2
    assert 'deferred' in caplog.text
    restarted = await store_factory(tmp_path)
    await restarted.sync(_catalog([_tool(description='2')]), embedding)
    assert len(restarted._client.list_collections()) == 2


async def test_pointer_publication_failure_does_not_switch_index(store_factory, tmp_path, monkeypatch):
    import os

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    before = store._collection.name
    def fail(*args, **kwargs):
        raise OSError('replace failed')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='changed')]), FakeEmbeddingClient())
    assert (await store_factory(tmp_path))._collection.name == before


async def test_query_rejects_vectors_from_a_different_model(store_factory, tmp_path):

    from sandbox.tools.retriever import ToolRetriever

    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    retriever = ToolRetriever(store, FakeEmbeddingClient('model-b'))
    result = await retriever.retrieve('query', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == 'failed' and result.error is not None


async def test_embedding_client_propagates_transport_failures(monkeypatch):

    from resources.embedding import EmbeddingClient

    async def fail(*args):
        raise OSError('offline')
    client = EmbeddingClient()
    monkeypatch.setattr(client, '_embed_async', fail)
    with pytest.raises(OSError):
        await client.embed(['search'])


@pytest.mark.parametrize('vectors', [[], [[1.0]], [[0.0, 0.0]], [[float('nan'), 1.0]], [[1.0, 2.0], [2.0, 3.0]]])
async def test_invalid_embedding_never_replaces_active_index(store_factory, tmp_path, vectors):
    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
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


async def test_routing_change_reembeds_tool(store_factory, tmp_path):
    from dataclasses import replace

    from sandbox.tools.registry import ToolDefinitionCatalog
    from sandbox.tools.routing.models import ToolRoutingSpec

    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    first = _catalog([_tool()])
    await store.sync(first, embedding)
    second = ToolDefinitionCatalog()
    second.add(_tool(), category='native', routing=replace(ToolRoutingSpec('test.search'), requires_workspace=True))
    await store.sync(second, embedding)
    assert len(embedding.calls) == 2


async def test_endpoint_and_format_changes_rebuild_before_query(store_factory, tmp_path, monkeypatch):
    from sandbox.tools import retriever as module

    embedding = FakeEmbeddingClient()
    embedding.profile = {'provider': 'local', 'model': embedding.model, 'endpoint': 'http://first/v1'}
    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), embedding)
    initial = store._collection.name
    embedding.profile['endpoint'] = 'http://second/v1'
    await store.sync(_catalog([_tool()]), embedding)
    assert store._collection.name != initial
    second = store._collection.name
    monkeypatch.setattr(module, '_INDEX_FORMAT', 3)
    restarted = await store_factory(tmp_path)
    await restarted.sync(_catalog([_tool()]), embedding)
    assert restarted._collection.name != second
    assert len(embedding.calls) == 3
    assert (await restarted.query(embedding, [[1., 1.]], n_results=1))['ids'] == [['search']]


async def test_restart_falls_back_to_valid_previous_and_rejects_wrong_profile(store_factory, tmp_path):
    import json

    from sandbox.tools.retriever import ToolRetriever

    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient('model-a'))
    previous = store._collection.name
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient('model-b'))
    store._collection.delete(ids=['search'])
    restarted = await store_factory(tmp_path)
    assert restarted._collection.name == previous
    result = await ToolRetriever(restarted, FakeEmbeddingClient('model-b')).retrieve(
        'query', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == 'failed'
    assert result.error.code == 'tool_retrieval_profile_mismatch'
    assert json.loads((tmp_path / 'active.json').read_text())['previous'] == previous


async def test_failure_after_pointer_replace_restores_old_publication(store_factory, tmp_path, monkeypatch):
    import os

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
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
    assert (await store_factory(tmp_path))._collection.name == store._collection.name




@pytest.mark.parametrize('vectors', [[], [[1.]], [[0., 0.]], [[1., 2.], [1., 2.]]])
async def test_invalid_query_embedding_is_a_retrieval_error(store_factory, tmp_path, vectors):
    from sandbox.tools.retriever import ToolRetriever

    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    async def malformed(documents):
        return vectors
    embedding.embed = malformed
    result = await ToolRetriever(store, embedding).retrieve('query', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == 'failed' and result.error is not None


async def test_empty_catalog_can_later_gain_tools(store_factory, tmp_path):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([]), embedding)
    await store.sync(_catalog([_tool()]), embedding)
    assert (await store.query(embedding, [[1., 1.]], n_results=1))['ids'] == [['search']]


async def test_pointer_records_non_secret_model_identity_and_dimension(store_factory, tmp_path):
    import json

    from resources.embedding import EmbeddingClient

    embedding = FakeEmbeddingClient()
    embedding.profile = EmbeddingClient(base_url='https://user:secret@host:443/v1?key=secret',
                                        model='model-a', provider='local').profile
    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), embedding)
    pointer = json.loads((tmp_path / 'active.json').read_text())
    assert pointer['profile'] == {'provider': 'local', 'model': 'model-a',
                                  'endpoint': 'https://host:443/v1', 'dimension': 2, 'format': 2}
    assert 'secret' not in json.dumps(pointer)


async def test_partial_batch_write_never_mutates_old_index(store_factory, tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
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
    assert (await store_factory(tmp_path))._collection.name == store._collection.name


async def test_staging_validation_rejects_corrupted_definition_hash(store_factory, tmp_path, monkeypatch):
    from chromadb.api.models.Collection import Collection

    from sandbox.tools.routing.models import ToolRetrievalError

    store = await store_factory(tmp_path)
    await store.sync(_catalog([_tool()]), FakeEmbeddingClient())
    old = store._collection.name
    original = Collection.upsert
    def corrupt(self, **kwargs):
        kwargs['metadatas'][0]['definition_hash'] = 'corrupted'
        return original(self, **kwargs)
    monkeypatch.setattr(Collection, 'upsert', corrupt)
    with pytest.raises(ToolRetrievalError):
        await store.sync(_catalog([_tool(description='new')]), FakeEmbeddingClient())
    assert (await store_factory(tmp_path))._collection.name == old


async def test_cleanup_enumeration_failure_does_not_undo_successful_publication(store_factory, tmp_path, monkeypatch, caplog):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    before = store._collection.name
    def unavailable(self, *args, **kwargs):
        raise OSError('collection enumeration unavailable')
    monkeypatch.setattr(type(store._client), 'list_collections', unavailable)
    await store.sync(_catalog([_tool(description='new')]), embedding)
    assert store._collection.name != before
    assert (await store_factory(tmp_path))._collection.name == store._collection.name
    assert 'deferred' in caplog.text


class BlockingChroma:
    """In-memory Chroma with controlled physical operations and thread observations."""

    def __init__(self):
        self.collections = {}
        self.calls = []
        self.block_operation = None
        self.entered = Event()
        self.release = Event()
        self.finished = Event()
        self.fail_operation = None
        self.query_calls = []

    def arm(self, operation):
        self.block_operation = operation
        self.entered.clear()
        self.release.clear()
        self.finished.clear()

    def visit(self, operation):
        self.calls.append((operation, get_ident()))
        if operation == self.block_operation:
            self.entered.set()
            assert self.release.wait(5), f'{operation} was never released'
            self.finished.set()
        if operation == self.fail_operation:
            raise OSError(f'{operation} failed')

    def client(self, **kwargs):
        self.visit('initialize')
        return self

    def create_collection(self, name, metadata):
        self.visit('create')
        collection = FakeCollection(self, name, metadata)
        self.collections[name] = collection
        return collection

    def get_collection(self, name):
        self.visit('get_collection')
        return self.collections[name]

    def list_collections(self):
        self.visit('list')
        return list(self.collections.values())

    def delete_collection(self, name):
        self.visit('delete')
        del self.collections[name]

    def get_max_batch_size(self):
        self.visit('batch_size')
        return 1


class FakeCollection:
    def __init__(self, owner, name, metadata):
        self.owner = owner
        self.name = name
        self._metadata = metadata
        self.rows = {}

    @property
    def metadata(self):
        self.owner.visit('metadata')
        return self._metadata

    def upsert(self, ids, documents, metadatas, embeddings):
        self.owner.visit('upsert')
        self.rows.update({name: (doc, meta, vector) for name, doc, meta, vector in
                          zip(ids, documents, metadatas, embeddings)})

    def get(self, **kwargs):
        self.owner.visit('get')
        return {'ids': list(self.rows),
                'documents': [row[0] for row in self.rows.values()],
                'metadatas': [row[1] for row in self.rows.values()],
                'embeddings': [row[2] for row in self.rows.values()]}

    def query(self, **kwargs):
        self.owner.visit('query')
        assert self.name in self.owner.collections, 'query lost its physical collection'
        self.owner.query_calls.append(deepcopy(kwargs))
        names = kwargs['where']['tool_name']['$in'][:kwargs['n_results']]
        return {'ids': [names], 'documents': [[f'doc {name}' for name in names]],
                'distances': [[0.0 for _ in names]]}


@pytest.fixture
def fake_chroma(monkeypatch):
    fake = BlockingChroma()
    monkeypatch.setattr('sandbox.tools.retriever.chromadb.PersistentClient', fake.client)
    return fake


async def _until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


def _blocking_watchdog(fake, operation):
    # A watchdog releases a blocking call even if the event loop is stuck.
    # The ticker must run while the physical call is still waiting.
    fake.arm(operation)
    watchdog = Thread(target=lambda: (fake.entered.wait(3), fake.release.wait(.3), fake.release.set()))
    watchdog.start()
    return watchdog



@pytest.mark.parametrize('operation', ['initialize', 'get', 'metadata', 'upsert', 'publish', 'query', 'list'])
async def test_chroma_and_publication_leave_event_loop(store_factory, tmp_path, fake_chroma, monkeypatch, operation):
    fake = fake_chroma
    loop_thread = get_ident()
    original_publish = ToolVectorStore._publish

    def publish(self, pointer):
        fake.visit('publish')
        original_publish(self, pointer)

    monkeypatch.setattr(ToolVectorStore, '_publish', publish)
    embedding = FakeEmbeddingClient()
    network_threads = []
    original_embed = embedding.embed

    async def embed(documents):
        network_threads.append(get_ident())
        await asyncio.sleep(0)
        return await original_embed(documents)

    embedding.embed = embed
    store = None
    if operation != 'initialize':
        store = await store_factory(tmp_path)
        await store.sync(_catalog([_tool()]), embedding)
    watchdog = _blocking_watchdog(fake, operation)
    if operation == 'initialize':
        task = asyncio.create_task(store_factory(tmp_path))
    elif operation == 'query':
        task = asyncio.create_task(ToolRetriever(store, embedding).retrieve(
            'search', allowed_tool_names=frozenset({'search'}), limit=1))
    else:
        task = asyncio.create_task(store.sync(_catalog([_tool(description='changed')]), embedding))
    ticks = 0
    try:
        await _until(fake.entered.is_set)
        for _ in range(5):
            await asyncio.sleep(0)
            ticks += 1
        assert ticks == 5 and not fake.finished.is_set()
    finally:
        fake.release.set()
        await task
        watchdog.join(1)
    assert all(thread != loop_thread for _, thread in fake.calls)
    assert len({thread for _, thread in fake.calls}) == 1
    assert all(thread == loop_thread for thread in network_threads)


async def test_pool_capacity_tracks_running_and_queued_physical_tasks(store_factory, tmp_path, fake_chroma):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    fake_chroma.arm('query')

    async def query():
        return await store.query(embedding, [[1., 1.]], n_results=1,
                                 where={'tool_name': {'$in': ['search']}})

    running = asyncio.create_task(query())
    queued = []
    try:
        await _until(fake_chroma.entered.is_set)
        queued = [asyncio.create_task(query()) for _ in range(7)]
        await asyncio.sleep(0)
        with pytest.raises(ToolRetrievalError) as full:
            await query()
        assert full.value.code == 'tool_retrieval_pool_saturated'
        for task in (running, queued[0]):
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ToolRetrievalError) as still_full:
            await query()
        assert still_full.value.code == 'tool_retrieval_pool_saturated'
        # Saturation degrades to the current allowed candidates at selection.
        from sandbox.tools.registry import ToolRegistry
        from sandbox.tools.routing.models import RuntimeCapabilitySnapshot, ToolRoutingSpec
        from sandbox.tools.routing.router import ToolRouter

        registry = ToolRegistry()
        registry.register(_tool(), routing=ToolRoutingSpec('test.search'))
        registry.freeze()
        selection = await ToolRouter(registry, ToolRetriever(store, embedding)).select(
            query='search', hints=(), runtime=RuntimeCapabilitySnapshot('web', False, False, ()),
            final_limit=1,
        )
        assert selection.status == 'degraded'
        assert selection.error.code == 'tool_retrieval_pool_saturated'
        assert selection.audit['final_tools'] == ['search']
    finally:
        fake_chroma.release.set()
        await asyncio.gather(running, *queued, return_exceptions=True)
    try:
        assert len(fake_chroma.query_calls) == 8
        assert (await query())['ids'] == [['search']]
    finally:
        await store.aclose()


async def test_query_parameters_and_reranker_are_request_local(store_factory, tmp_path, fake_chroma):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    other = _tool().model_copy(update={'name': 'other'})
    await store.sync(_catalog([_tool(), other]), embedding)
    loop_thread = get_ident()

    class Reranker:
        async def rerank(self, query, documents, top_n):
            assert get_ident() == loop_thread
            await asyncio.sleep(0)
            from types import SimpleNamespace
            return [SimpleNamespace(index=0, score=.9)]

    first, second = await asyncio.gather(
        ToolRetriever(store, embedding, Reranker()).retrieve(
            'first', allowed_tool_names=frozenset({'search', 'other'}), limit=1),
        ToolRetriever(store, embedding).retrieve(
            'second', allowed_tool_names=frozenset({'search'}), limit=1),
    )
    assert [c.tool_name for c in first.candidates] == ['other']
    assert [c.tool_name for c in second.candidates] == ['search']
    assert fake_chroma.query_calls[0]['n_results'] == 2
    assert fake_chroma.query_calls[1]['where'] == {'tool_name': {'$in': ['search']}}


@pytest.mark.parametrize('operation', ['get', 'upsert', 'publish'])
async def test_cancelled_sync_keeps_write_guard_until_physical_completion(
    store_factory, tmp_path, fake_chroma, monkeypatch, operation,
):
    original_publish = ToolVectorStore._publish

    def publish(self, pointer):
        fake_chroma.visit('publish')
        original_publish(self, pointer)

    monkeypatch.setattr(ToolVectorStore, '_publish', publish)
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old = store._collection.name
    fake_chroma.arm(operation)
    task = asyncio.create_task(store.sync(_catalog([_tool(description='changed')]), embedding))
    try:
        await _until(fake_chroma.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ToolRetrievalError) as busy:
            await store.sync(_catalog([_tool(description='second')]), embedding)
        assert busy.value.code == 'tool_retrieval_sync_in_progress'
    finally:
        fake_chroma.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await _until(lambda: not store._syncing)
    if operation != 'get':
        assert store._collection.name != old
    await store.sync(_catalog([_tool(description='second')]), embedding)
    assert len(fake_chroma.collections) == 2


async def test_close_cancels_queued_tasks_waits_without_blocking_and_rejects_submissions(
    store_factory, tmp_path, fake_chroma,
):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    fake_chroma.arm('query')

    async def query():
        return await store.query(embedding, [[1., 1.]], n_results=1,
                                 where={'tool_name': {'$in': ['search']}})

    running = asyncio.create_task(query())
    queued = closing = None
    try:
        await _until(fake_chroma.entered.is_set)
        queued = asyncio.create_task(query())
        await asyncio.sleep(0)
        closing = asyncio.create_task(store.aclose())
        await _until(queued.done)
        assert queued.cancelled()
        assert not closing.done()
        with pytest.raises(ToolRetrievalError) as closed:
            await query()
        assert closed.value.code == 'tool_retrieval_store_closed'
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        closing = asyncio.create_task(store.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
    finally:
        fake_chroma.release.set()
        await asyncio.gather(*(task for task in (running, queued, closing) if task), return_exceptions=True)
        await store.aclose()
    assert len(fake_chroma.query_calls) == 1


async def test_cancelled_physical_query_cannot_release_old_collection(store_factory, tmp_path, fake_chroma):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old = store._collection.name
    fake_chroma.arm('query')
    query = asyncio.create_task(store.query(embedding, [[1., 1.]], n_results=1,
                                           where={'tool_name': {'$in': ['search']}}))
    sync = None
    try:
        await _until(fake_chroma.entered.is_set)
        query.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query
        sync = asyncio.create_task(store.sync(_catalog([_tool(description='first')]), embedding))
        await asyncio.sleep(0)
        assert not sync.done()
        assert old in fake_chroma.collections
    finally:
        fake_chroma.release.set()
        await asyncio.gather(*(task for task in (query, sync) if task), return_exceptions=True)
    for description in ('second', 'third'):
        await store.sync(_catalog([_tool(description=description)]), embedding)
    assert old not in fake_chroma.collections
    assert len(fake_chroma.collections) == 2


async def test_initialization_failure_shuts_down_private_pool(tmp_path, fake_chroma, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    shutdown = []
    original = ThreadPoolExecutor.shutdown

    def record(self, *args, **kwargs):
        shutdown.append(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, 'shutdown', record)
    fake_chroma.fail_operation = 'initialize'
    with pytest.raises(ToolRetrievalError):
        async with ToolVectorStore(str(tmp_path)):
            pytest.fail('failed initialization was accepted')
    assert shutdown and all(call.get('wait') is False for call in shutdown)


@pytest.mark.parametrize('failure', ['initialize', 'sync', 'later_startup', None])
async def test_worker_dependency_stack_cleans_store_on_failure_and_normal_close(
    tmp_path, fake_chroma, monkeypatch, failure,
):
    from types import SimpleNamespace

    from bootstrap import worker_dependencies as module
    from orchestration.config import AppConfig

    # This Worker composition fixture has no file service; isolate its flags
    # from host configuration and earlier tests that load environment defaults.
    monkeypatch.setenv('FILE_CAPABILITY_ENABLED', 'false')
    monkeypatch.setenv('FILE_TRANSFORM_ENABLED', 'false')
    monkeypatch.setenv('FILE_SHELL_ENABLED', 'false')
    config = AppConfig()
    config.models.tool_rag.enabled = True
    config.models.tool_rag.persist_path = str(tmp_path)
    config.models.mcp.auto_connect = False
    config.models.skills.enabled = False
    monkeypatch.setattr('resources.embedding.create_embedding_client', lambda _: FakeEmbeddingClient())
    monkeypatch.setattr('resources.reranker.create_reranker_client', lambda _: None)
    stores = []
    original = ToolVectorStore.__init__

    def record(self, *args, **kwargs):
        original(self, *args, **kwargs)
        stores.append(self)

    monkeypatch.setattr(ToolVectorStore, '__init__', record)
    if failure == 'initialize':
        fake_chroma.fail_operation = 'initialize'
    elif failure == 'sync':
        fake_chroma.fail_operation = 'upsert'

    async def initialize(config, stack):
        _, _, retriever = await module.setup_tools(config, resource_stack=stack)
        await retriever._store.sync(_catalog([_tool()]), retriever._embedding)
        if failure == 'later_startup':
            raise ValueError('later startup failed')
        return SimpleNamespace(resource_stack=stack.pop_all())

    monkeypatch.setattr(module, '_init_dependencies', initialize)
    if failure:
        with pytest.raises((ToolRetrievalError, ValueError)):
            await module.init_dependencies(config)
    else:
        dependencies = await module.init_dependencies(config)
        assert not stores[0]._closed
        await module.WorkerDependencies.close(dependencies)
    assert stores[0]._closed
    assert not stores[0]._pending
    with pytest.raises(ToolRetrievalError) as closed:
        await stores[0].sync(_catalog([]), FakeEmbeddingClient())
    assert closed.value.code == 'tool_retrieval_store_closed'


@pytest.mark.parametrize('operation', ['metadata', 'list'])
async def test_submission_never_waits_on_a_lock_held_by_chroma(
    store_factory, tmp_path, fake_chroma, operation,
):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    watchdog = _blocking_watchdog(fake_chroma, operation)

    async def query():
        return await store.query(embedding, [[1., 1.]], n_results=1,
                                 where={'tool_name': {'$in': ['search']}})

    blocking = asyncio.create_task(query() if operation == 'metadata' else
                                   store.sync(_catalog([_tool()]), embedding))
    queued = None
    try:
        await _until(fake_chroma.entered.is_set)
        queued = asyncio.create_task(query())
        await asyncio.sleep(0)
        assert not fake_chroma.finished.is_set()
    finally:
        fake_chroma.release.set()
        await asyncio.gather(*(t for t in (blocking, queued) if t), return_exceptions=True)
        watchdog.join(1)


async def test_queued_query_copies_mutable_parameters_before_submission(store_factory, tmp_path, fake_chroma):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    fake_chroma.arm('query')
    vectors = [[1., 1.]]
    where = {'tool_name': {'$in': ['search']}}
    first = asyncio.create_task(store.query(embedding, [[1., 1.]], n_results=1, where=deepcopy(where)))
    second = None
    try:
        await _until(fake_chroma.entered.is_set)
        second = asyncio.create_task(store.query(embedding, vectors, n_results=1, where=where))
        await asyncio.sleep(0)
        vectors[0][0] = 99.
        where['tool_name']['$in'][0] = 'unauthorized'
    finally:
        fake_chroma.release.set()
        await asyncio.gather(*(t for t in (first, second) if t))
    assert fake_chroma.query_calls[1]['query_embeddings'] == [[1., 1.]]
    assert fake_chroma.query_calls[1]['where'] == {'tool_name': {'$in': ['search']}}


@pytest.mark.parametrize('write_started', [False, True])
async def test_close_racing_sync_cancels_only_unstarted_physical_work(
    store_factory, tmp_path, fake_chroma, write_started,
):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old = store._collection.name
    fake_chroma.arm('upsert' if write_started else 'query')
    blocker = None
    if not write_started:
        blocker = asyncio.create_task(store.query(embedding, [[1., 1.]], n_results=1,
                                                  where={'tool_name': {'$in': ['search']}}))
        await _until(fake_chroma.entered.is_set)
    sync = asyncio.create_task(store.sync(_catalog([_tool(description='changed')]), embedding))
    closing = None
    try:
        if write_started:
            await _until(fake_chroma.entered.is_set)
        else:
            await asyncio.sleep(0)
        closing = asyncio.create_task(store.aclose())
        await _until(lambda: store._closed)
        await asyncio.sleep(0)
        assert not closing.done()
        if not write_started:
            await _until(sync.done)
            assert sync.cancelled()
    finally:
        fake_chroma.release.set()
        await asyncio.gather(*(task for task in (blocker, sync, closing) if task), return_exceptions=True)
        await store.aclose()
    assert not store._syncing
    assert (store._collection.name != old) == write_started
    import json

    assert json.loads((tmp_path / 'active.json').read_text())['current'] == store._collection.name


async def test_close_during_embedding_prevents_late_write(store_factory, tmp_path, fake_chroma):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    old = store._collection.name
    entered, release = asyncio.Event(), asyncio.Event()

    async def embed(documents):
        entered.set()
        await release.wait()
        return [[1., 1.] for _ in documents]

    embedding.embed = embed
    sync = asyncio.create_task(store.sync(_catalog([_tool(description='changed')]), embedding))
    try:
        await entered.wait()
        await store.aclose()
    finally:
        release.set()
    with pytest.raises(ToolRetrievalError) as closed:
        await sync
    assert closed.value.code == 'tool_retrieval_store_closed'
    assert store._collection.name == old
    assert not store._syncing


async def test_cancelled_initialization_waits_for_physical_cleanup_without_blocking(tmp_path, fake_chroma):
    fake_chroma.arm('initialize')
    stack = AsyncExitStack()
    store = ToolVectorStore(str(tmp_path))
    opening = asyncio.create_task(stack.enter_async_context(store))
    try:
        await _until(fake_chroma.entered.is_set)
        opening.cancel()
        await _until(lambda: store._closed)
        assert not opening.done()
    finally:
        fake_chroma.release.set()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert not store._pending
    await stack.aclose()


@pytest.mark.parametrize('stage', ['embedding', 'query', 'reranker'])
@pytest.mark.parametrize('fault,expected', [
    (TimeoutError(), 'degraded'), (ConnectionError(), 'degraded'),
    (PermissionError(), 'failed'), (ValueError('invalid configuration'), 'failed'),
    (TypeError('program error'), 'failed'), (RuntimeError('program error'), 'failed'),
    (asyncio.CancelledError(), 'cancelled'),
])
async def test_retriever_fault_classification_and_chroma_order(stage, fault, expected):
    class Embedding(FakeEmbeddingClient):
        async def embed(self, texts):
            if stage == 'embedding':
                raise fault
            return await super().embed(texts)

    class Store:
        async def query(self, *args, **kwargs):
            if stage == 'query':
                raise fault
            return {'ids': [['zeta', 'alpha']], 'documents': [['z', 'a']], 'distances': [[0.0, .2]]}

    class Reranker:
        async def rerank(self, *args, **kwargs):
            if stage == 'reranker':
                raise fault
            from resources.reranker import RerankResult
            return [RerankResult(1, .9)]

    call = ToolRetriever(Store(), Embedding(), Reranker()).retrieve(
        'q', allowed_tool_names=frozenset({'zeta', 'alpha'}), limit=1)
    if expected == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            await call
        return
    result = await call
    assert result.status == expected
    assert result.error.stage == stage
    if stage == 'reranker' and expected == 'degraded':
        assert [(c.tool_name, c.score) for c in result.candidates] == [('zeta', 1.0)]
    else:
        assert result.candidates == ()


@pytest.mark.parametrize('status,expected', [(401, 'failed'), (403, 'failed'), (400, 'failed'), (429, 'degraded'), (503, 'degraded')])
async def test_embedding_http_failures_are_classified(status, expected):
    import httpx

    class Embedding(FakeEmbeddingClient):
        async def embed(self, texts):
            response = httpx.Response(status, request=httpx.Request('POST', 'https://test.invalid'))
            response.raise_for_status()

    result = await ToolRetriever(None, Embedding()).retrieve('q', allowed_tool_names=frozenset({'alpha'}), limit=1)
    assert result.status == expected


async def test_empty_retrieval_does_not_claim_no_eligible_tools():
    class Store:
        async def query(self, *args, **kwargs):
            return {'ids': [[]], 'documents': [[]], 'distances': [[]]}

    retriever = ToolRetriever(Store(), FakeEmbeddingClient())
    assert (await retriever.retrieve('q', allowed_tool_names=frozenset(), limit=1)).status == 'no_eligible_tools'
    assert (await retriever.retrieve('q', allowed_tool_names=frozenset({'alpha'}), limit=1)).status == 'success'


async def test_rag_initialization_failure_is_distinct_from_disabled(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bootstrap.worker_dependencies import setup_tools
    from orchestration.config import AppConfig

    config = AppConfig()
    config.models.mcp.auto_connect = False
    config.models.skills.enabled = False
    config.models.tool_rag.enabled = False
    async with AsyncExitStack() as stack:
        _, _, retriever = await setup_tools(config, resource_stack=stack)
    assert retriever is None
    config.models.tool_rag.enabled = True
    config.models.tool_rag.persist_path = str(tmp_path)
    monkeypatch.setattr('resources.embedding.create_embedding_client', lambda _: SimpleNamespace())
    def invalid(_):
        raise ValueError('invalid reranker provider')
    monkeypatch.setattr('resources.reranker.create_reranker_client', invalid)
    async with AsyncExitStack() as stack:
        with pytest.raises(ToolRetrievalError) as error:
            await setup_tools(config, resource_stack=stack)
    assert error.value.stage == 'initialize' and not error.value.recoverable


async def test_unknown_reranker_provider_is_configuration_failure():
    from types import SimpleNamespace

    from resources.reranker import create_reranker_client

    config = SimpleNamespace(rerank=SimpleNamespace(provider='missing', model='rerank', timeout=1), providers={})
    with pytest.raises(ValueError):
        create_reranker_client(config)


@pytest.mark.parametrize('fault,expected', [
    (TimeoutError(), 'degraded'), (PermissionError(), 'failed'), (TypeError('bug'), 'failed'),
])
async def test_chroma_worker_preserves_failure_classification(store_factory, tmp_path, monkeypatch, fault, expected):
    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    def fail(*args, **kwargs):
        raise fault
    monkeypatch.setattr(type(store._collection), 'query', fail)
    result = await ToolRetriever(store, embedding).retrieve('q', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == expected
    assert result.error.stage == 'query'


@pytest.mark.parametrize('kind,expected', [('InternalError', 'degraded'), ('RateLimitError', 'degraded'), ('AuthorizationError', 'failed'), ('InvalidArgumentError', 'failed')])
async def test_chroma_typed_errors_are_not_all_recoverable(store_factory, tmp_path, monkeypatch, kind, expected):
    import chromadb.errors

    store = await store_factory(tmp_path)
    embedding = FakeEmbeddingClient()
    await store.sync(_catalog([_tool()]), embedding)
    def fail(*args, **kwargs):
        raise getattr(chromadb.errors, kind)('injected Chroma error')
    monkeypatch.setattr(type(store._collection), 'query', fail)
    result = await ToolRetriever(store, embedding).retrieve('q', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == expected
    assert result.error.stage == 'query'
    if kind == 'AuthorizationError':
        assert result.error.code == 'tool_retrieval_permission_denied'


async def test_retriever_rejects_unauthorized_candidate_before_truncation():
    class Store:
        async def query(self, *args, **kwargs):
            return {'ids': [['search', 'forbidden']], 'documents': [['s', 'f']], 'distances': [[0., .2]]}

    result = await ToolRetriever(Store(), FakeEmbeddingClient()).retrieve('q', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == 'failed' and result.candidates == ()
    assert result.error.code == 'tool_retrieval_permission_denied'


@pytest.mark.parametrize('distance', [float('nan'), float('inf'), None, 'invalid'])
async def test_invalid_chroma_scores_fail_instead_of_becoming_success(distance):
    class Store:
        async def query(self, *args, **kwargs):
            return {'ids': [['search']], 'documents': [['s']], 'distances': [[distance]]}

    result = await ToolRetriever(Store(), FakeEmbeddingClient()).retrieve('q', allowed_tool_names=frozenset({'search'}), limit=1)
    assert result.status == 'failed' and result.candidates == ()
