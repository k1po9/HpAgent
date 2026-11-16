"""Worker-owned Chroma index with bounded I/O and atomic publication."""
import asyncio
import hashlib
import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4

import chromadb
from chromadb.config import Settings

from resources.embedding import validate_embeddings
from sandbox.tools.registry import ToolDefinitionCatalog, _tool_definition_hash
from sandbox.tools.routing.models import (
    ScoredCandidate,
    SemanticRetrievalResult,
    ToolRetrievalError,
    retrieval_failure,
)

logger = logging.getLogger("HpAgent.ToolRAG")
_INDEX_FORMAT = 2
_COLLECTION_PREFIX = "tool_staging_"


def _identity(embedding) -> dict:
    return {**getattr(embedding, "profile", {"model": embedding.model}), "format": _INDEX_FORMAT}


def _manifest(hashes: dict) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _document(definition: dict) -> str:
    fields = definition["parameters"].get("properties", {})
    return f"{definition['name']}: {definition['description']}" + "".join(
        f" {name}: {field.get('description', '')}" for name, field in sorted(fields.items())
    )


class ToolVectorStore:
    """One writer per directory; Execution registries never synchronize this store."""

    def __init__(self, persist_path: str = "tools/vectors"):
        self._path = Path(persist_path)
        self._lock = RLock()
        self._syncing = False
        self._active_queries = 0
        self._borrowed = False
        self._pointer = {"current": None, "previous": None}
        self._collection = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool-vector-store")
        self._pending = set()
        self._closed = False
        self._close_task = None
        self._client = None

    async def __aenter__(self):
        try:
            await self._wait(self._submit(self._initialize))
            return self
        except BaseException as exc:
            await self.aclose()
            if isinstance(exc, Exception) and not isinstance(exc, ToolRetrievalError):
                raise ToolRetrievalError("initialize", "initialization_failed") from exc
            raise

    async def __aexit__(self, *exc):
        await self.aclose()

    def _submit(self, function, *args):
        with self._lock:
            if self._closed:
                raise ToolRetrievalError("store", "store_closed")
            if len(self._pending) >= 8:
                raise ToolRetrievalError("store", "pool_saturated", recoverable=True)
            future = self._executor.submit(function, *args)
            self._pending.add(future)
            future.add_done_callback(self._finished)
            return future

    def _finished(self, future):
        with self._lock:
            self._pending.discard(future)

    @staticmethod
    async def _wait(future):
        wrapped = asyncio.wrap_future(future)
        # A cancelled awaiter neither cancels the physical task nor leaves its
        # later exception unobserved. Capacity belongs to the concurrent Future.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(wrapped)

    async def aclose(self):
        if self._close_task is None:
            with self._lock:
                self._closed = True
                pending = tuple(self._pending)
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._close_task = asyncio.create_task(self._drain(pending))
        await asyncio.shield(self._close_task)

    @staticmethod
    async def _drain(pending):
        await asyncio.gather(*(asyncio.wrap_future(future) for future in pending),
                             return_exceptions=True)

    def _initialize(self):
        self._client = chromadb.PersistentClient(
            path=str(self._path), settings=Settings(anonymized_telemetry=False),
        )
        pointer = self._path / "active.json"
        if pointer.exists():
            try:
                self._pointer = json.loads(pointer.read_text())
                for slot in ("current", "previous"):
                    name = self._pointer.get(slot)
                    if not name:
                        continue
                    try:
                        collection = self._client.get_collection(name)
                        self._validate(collection)
                        recorded_profile = self._pointer.get("profile" if slot == "current" else "previous_profile")
                        if recorded_profile is not None and self._profile(collection) != recorded_profile:
                            raise ValueError("published profile mismatch")
                    except Exception:
                        logger.warning("catalog_sync: invalid published collection", exc_info=True)
                        continue
                    self._collection = collection
                    break
                if self._collection is None:
                    raise ValueError("no validated published collection")
            except Exception as exc:
                raise ToolRetrievalError("initialize", "index_invalid") from exc

    @staticmethod
    def _profile(collection) -> dict:
        return json.loads((collection.metadata or {})["profile"])

    def _validate(self, collection, expected: dict | None = None) -> None:
        profile = self._profile(collection)
        result = collection.get(include=["metadatas", "embeddings", "documents"])
        ids = result["ids"]
        metadata = result["metadatas"]
        vectors = result["embeddings"]
        if len(ids) != len(set(ids)) or len(metadata) != len(ids) or len(vectors) != len(ids):
            raise ValueError("index count mismatch")
        hashes = {}
        for name, meta, document, vector in zip(ids, metadata, result["documents"], vectors):
            definition = json.loads(meta["definition"])
            digest = _tool_definition_hash(definition)
            if (name != definition["name"] or meta["tool_name"] != name
                    or digest != meta["definition_hash"] or _document(definition) != document):
                raise ValueError("index declaration mismatch")
            validate_embeddings([list(vector)], 1, profile["dimension"])
            hashes[name] = digest
        if hashes != expected and expected is not None:
            raise ValueError("index names/hash/count mismatch")
        if _manifest(hashes) != collection.metadata["manifest"]:
            raise ValueError("index manifest mismatch")
        if not isinstance(profile["format"], int) or profile["dimension"] < 0:
            raise ValueError("invalid index profile")

    async def sync(self, catalog: ToolDefinitionCatalog, embedding_client) -> None:
        if not isinstance(catalog, ToolDefinitionCatalog):
            raise TypeError("sync requires a ToolDefinitionCatalog with source completeness")
        with self._lock:
            if self._closed:
                raise ToolRetrievalError("store", "store_closed")
            if self._syncing:
                raise ToolRetrievalError("sync", "sync_in_progress")
            self._syncing = True
        future = None
        try:
            definitions = {d["name"]: d for d in catalog.snapshot()}
            future = self._submit(self._read_index)
            previous, old_profile, old_rows = await self._wait(future)
            identity = _identity(embedding_client)
            compatible = old_profile is not None and all(old_profile.get(k) == v for k, v in identity.items())
            if not catalog.complete:
                if previous is None:
                    raise ToolRetrievalError("sync", "catalog_incomplete")
                # Preserve declarations from failed sources, including on a model rebuild.
                for name, (_, meta, _) in old_rows.items():
                    definitions.setdefault(name, json.loads(meta["definition"]))
                logger.warning("catalog_sync: incomplete sources; deletion disabled")
            hashes = {name: _tool_definition_hash(d) for name, d in definitions.items()}
            changed = [name for name in sorted(definitions)
                       if not compatible or name not in old_rows
                       or old_rows[name][1]["definition_hash"] != hashes[name]]
            if compatible and not changed and set(old_rows) == set(definitions):
                future = self._submit(self._cleanup)
                await self._wait(future)
                return
            documents = [_document(definitions[name]) for name in changed]
            dimension = (old_profile["dimension"] or None) if compatible else None
            try:
                vectors = await embedding_client.embed(documents) if documents else []
                dimension = validate_embeddings(vectors, len(documents), dimension)
            except Exception as exc:
                raise ToolRetrievalError("embedding", "embedding_invalid") from exc
            profile = {**identity, "dimension": dimension}
            rows = {}
            new_vectors = dict(zip(changed, vectors))
            for name, definition in definitions.items():
                vector = new_vectors[name] if name in new_vectors else old_rows[name][2]
                rows[name] = (_document(definition), {
                    "tool_name": name, "category": definition["category"],
                    "definition_hash": hashes[name],
                    "definition": json.dumps(definition, ensure_ascii=False, sort_keys=True),
                }, vector)
            future = self._submit(self._write_index, rows, hashes, profile,
                                  embedding_client.model, previous, old_profile)
            await self._wait(future)
            logger.info("catalog_sync: published tools=%d embedded=%d complete=%s",
                        len(rows), len(changed), catalog.complete)
        except ToolRetrievalError:
            raise
        except Exception as exc:
            raise ToolRetrievalError("sync", "index_update_failed") from exc
        finally:
            # Cancellation only releases the writer once its last physical I/O
            # has finished, including staging rollback/publication cleanup.
            if future is not None and not future.done():
                future.add_done_callback(self._finish_sync)
            else:
                self._finish_sync(future)

    def _finish_sync(self, _future):
        with self._lock:
            self._syncing = False

    def _read_index(self):
        previous = self._collection
        profile = self._profile(previous) if previous is not None else None
        old = previous.get(include=["documents", "metadatas", "embeddings"]) if previous is not None else None
        rows = {
            name: (doc, meta, list(vector))
            for name, doc, meta, vector in zip(old["ids"], old["documents"], old["metadatas"], old["embeddings"])
        } if old else {}
        return previous, profile, rows

    def _write_index(self, rows, hashes, profile, model, previous, old_profile):
        staging = None
        try:
            staging = self._client.create_collection(
                name=_COLLECTION_PREFIX + uuid4().hex,
                metadata={"hnsw:space": "cosine", "profile": json.dumps(profile, sort_keys=True),
                          "embedding_model": model, "manifest": _manifest(hashes)},
            )
            names = sorted(rows)
            batch_size = min(5000, self._client.get_max_batch_size())
            for start in range(0, len(names), batch_size):
                batch = names[start:start + batch_size]
                staging.upsert(ids=batch, documents=[rows[n][0] for n in batch],
                               metadatas=[rows[n][1] for n in batch],
                               embeddings=[rows[n][2] for n in batch])
            self._validate(staging, hashes)
            pointer = {"current": staging.name, "profile": profile,
                       "previous": previous.name if previous is not None else None,
                       "previous_profile": old_profile}
            self._publish(pointer)
            with self._lock:
                self._collection = staging
                self._pointer = pointer
            staging = None
            self._cleanup()
        except Exception as exc:
            raise ToolRetrievalError("sync", "index_update_failed") from exc
        finally:
            if staging is not None:
                # Failed staging is never exposed to queries before publication.
                try:
                    published = json.loads((self._path / "active.json").read_text())
                except (OSError, ValueError):
                    published = {}
                if staging.name not in (published.get("current"), published.get("previous")):
                    try:
                        self._client.delete_collection(staging.name)
                    except Exception:
                        logger.warning("catalog_sync: staging cleanup deferred", exc_info=True)

    def _publish(self, pointer: dict) -> None:
        target = self._path / "active.json"
        temporary = self._path / ("active-" + uuid4().hex + ".tmp")
        backup = self._path / ("active-" + uuid4().hex + ".bak")
        replaced = False
        directory = os.open(self._path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if target.exists():
                os.link(target, backup)
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(pointer, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
            os.replace(temporary, target)
            replaced = True
            os.fsync(directory)
        except BaseException:
            if replaced:
                if backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink()
                os.fsync(directory)
            raise
        finally:
            os.close(directory)
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)

    def _cleanup(self) -> None:
        with self._lock:
            if self._active_queries or self._borrowed:
                logger.warning("catalog_sync: cleanup deferred; active query or unconfirmed borrowed handle")
                return
            keep = {self._pointer.get("current"), self._pointer.get("previous")}
        try:
            collections = self._client.list_collections()
        except Exception:
            logger.warning("catalog_sync: collection enumeration cleanup deferred", exc_info=True)
            return
        for collection in collections:
            name = collection.name
            if (name.startswith(_COLLECTION_PREFIX) or name == "tool_definitions") and name not in keep:
                try:
                    self._client.delete_collection(name)
                except Exception:
                    logger.warning("catalog_sync: old collection cleanup deferred", exc_info=True)

    async def query(self, embedding_client, vectors, **kwargs):
        identity = deepcopy(_identity(embedding_client))
        future = self._submit(self._query, identity, deepcopy(vectors), deepcopy(kwargs))
        return await self._wait(future)

    def _query(self, identity, vectors, kwargs):
        with self._lock:
            collection = self._collection
            if collection is None:
                raise ToolRetrievalError("query", "index_missing", recoverable=True)
            self._active_queries += 1
        try:
            profile = self._profile(collection)
            if any(profile.get(k) != v for k, v in identity.items()):
                raise ToolRetrievalError("query", "profile_mismatch")
            try:
                validate_embeddings(vectors, 1, profile["dimension"])
            except Exception as exc:
                raise ToolRetrievalError("query", "embedding_invalid") from exc
            return collection.query(query_embeddings=vectors, **kwargs)
        except ToolRetrievalError:
            raise
        except (chromadb.errors.AuthorizationError, chromadb.errors.ChromaAuthError) as exc:
            raise ToolRetrievalError("query", "permission_denied") from exc
        except (chromadb.errors.InternalError, chromadb.errors.RateLimitError) as exc:
            raise ToolRetrievalError("query", "index_unavailable", recoverable=True) from exc
        except Exception as exc:
            raise retrieval_failure("query", exc) from exc
        finally:
            with self._lock:
                self._active_queries -= 1

    @property
    def collection(self):
        # Compatibility: a caller can retain this raw handle indefinitely. Never
        # infer its release from Task cancellation, refcounts, or a later sync.
        with self._lock:
            self._borrowed = True
            return self._collection


class ToolRetriever:
    """工具语义检索器 —— 基于用户查询检索 Top-K 相关工具。

    支持可选的 Reranker 精排：当提供 reranker_client 时，
    先从 ChromaDB 召回 top_n * 2 候选，再经 reranker 精排后返回 top_k。

    Retrieval is request-pure: scores are returned with candidates and are never
    stored on this shared service.
    """

    def __init__(self, vector_store: ToolVectorStore, embedding_client, reranker_client=None):
        self._store = vector_store
        self._embedding = embedding_client
        self._reranker = reranker_client

    async def retrieve(
        self,
        query: str,
        *,
        allowed_tool_names: frozenset[str],
        limit: int,
    ) -> SemanticRetrievalResult:
        if not allowed_tool_names:
            return SemanticRetrievalResult(status="no_eligible_tools")
        stage = "configuration"
        try:
            if limit <= 0:
                raise ValueError("semantic fetch limit must be positive")
            fetch_k = min(len(allowed_tool_names), limit * 2 if self._reranker else limit)
            stage = "embedding"
            vectors = await self._embedding.embed([query])
            validate_embeddings(vectors, 1)
            stage = "query"
            results = await self._store.query(
                self._embedding, vectors, n_results=fetch_k,
                where={"tool_name": {"$in": sorted(allowed_tool_names)}},
            )
            tool_names = results["ids"][0] if results["ids"] else []
            documents = results.get("documents", [[]])[0] if results.get("documents") else []
            distances = results.get("distances", [[]])[0] if results.get("distances") else []
            if any(name not in allowed_tool_names for name in tool_names):
                raise PermissionError("retrieval returned a disallowed candidate")
            if len(set(tool_names)) != len(tool_names) or len(distances) != len(tool_names):
                raise ValueError("invalid Chroma candidate response")
            if any(not math.isfinite(distance) for distance in distances):
                raise ValueError("invalid Chroma candidate distances")
            scores = {name: round(1.0 - distance, 4) for name, distance in zip(tool_names, distances)}
            chroma = tuple(ScoredCandidate(name, scores[name], rank, query)
                           for rank, name in enumerate(tool_names[:limit], 1))
            if self._reranker and len(tool_names) > limit:
                stage = "reranker"
                if len(documents) != len(tool_names):
                    raise ValueError("missing candidate documents")
                try:
                    reranked = await self._reranker.rerank(query, documents, top_n=limit)
                except Exception as exc:
                    error = retrieval_failure(stage, exc)
                    if error.recoverable:
                        return SemanticRetrievalResult(chroma, "degraded", error)
                    raise
                indices = [item.index for item in reranked]
                if any(isinstance(index, bool) or not isinstance(index, int) or
                       not 0 <= index < len(tool_names) for index in indices) or len(set(indices)) != len(indices):
                    raise ValueError("invalid reranker candidate indices")
                if any(not math.isfinite(item.score) for item in reranked):
                    raise ValueError("invalid reranker scores")
                # Preserve the existing low-confidence policy independently of dependency failure.
                if max((item.score for item in reranked), default=0.0) >= 0.05:
                    return SemanticRetrievalResult(tuple(
                        ScoredCandidate(tool_names[item.index], round(item.score, 4), rank, query)
                        for rank, item in enumerate(reranked[:limit], 1)))
            return SemanticRetrievalResult(chroma)
        except Exception as exc:
            error = retrieval_failure(stage, exc)
            return SemanticRetrievalResult(status="degraded" if error.recoverable else "failed", error=error)
