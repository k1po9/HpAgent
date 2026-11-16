"""Worker-owned Chroma index with validated staging and atomic publication.

Chroma I/O is still synchronous here. P2 moves it to the bounded private pool.
"""
import hashlib
import json
import logging
import os
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
        self._client = chromadb.PersistentClient(
            path=persist_path, settings=Settings(anonymized_telemetry=False),
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
            if self._syncing:
                raise ToolRetrievalError("sync", "sync_in_progress")
            self._syncing = True
        staging = None
        try:
            definitions = {d["name"]: d for d in catalog.snapshot()}
            previous = self._collection
            old_profile = self._profile(previous) if previous is not None else None
            old = previous.get(include=["documents", "metadatas", "embeddings"]) if previous is not None else None
            old_rows = {
                name: (doc, meta, list(vector))
                for name, doc, meta, vector in zip(old["ids"], old["documents"], old["metadatas"], old["embeddings"])
            } if old else {}
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
                self._cleanup()
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
            staging = self._client.create_collection(
                name=_COLLECTION_PREFIX + uuid4().hex,
                metadata={"hnsw:space": "cosine", "profile": json.dumps(profile, sort_keys=True),
                          "embedding_model": embedding_client.model, "manifest": _manifest(hashes)},
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
            logger.info("catalog_sync: published tools=%d embedded=%d complete=%s",
                        len(rows), len(changed), catalog.complete)
            staging = None
            self._cleanup()
        except ToolRetrievalError:
            raise
        except Exception as exc:
            raise ToolRetrievalError("sync", "index_update_failed") from exc
        finally:
            with self._lock:
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
                self._syncing = False

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

    def query(self, embedding_client, vectors, **kwargs):
        with self._lock:
            collection = self._collection
            if collection is None:
                raise ToolRetrievalError("query", "index_missing")
            profile = self._profile(collection)
            if any(profile.get(k) != v for k, v in _identity(embedding_client).items()):
                raise ToolRetrievalError("query", "profile_mismatch")
            try:
                validate_embeddings(vectors, 1, profile["dimension"])
            except Exception as exc:
                raise ToolRetrievalError("query", "embedding_invalid") from exc
            self._active_queries += 1
        try:
            return collection.query(query_embeddings=vectors, **kwargs)
        except Exception as exc:
            raise ToolRetrievalError("query", "index_unavailable") from exc
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
        if not allowed_tool_names or limit <= 0:
            return SemanticRetrievalResult()
        fetch_k = min(len(allowed_tool_names), limit * 2 if self._reranker else limit)
        logger.info(
            "retrieve: query=%s top_k=%d fetch_k=%d reranker=%s filter=%s",
            query[:80], limit, fetch_k, self._reranker is not None, "eligible_subset",
        )

        try:
            vectors = await self._embedding.embed([query])
            validate_embeddings(vectors, 1)
        except Exception as exc:
            raise ToolRetrievalError("embedding", "embedding_invalid") from exc
        query_embedding = vectors[0]
        logger.info("retrieve: embedding dim=%d", len(query_embedding) if query_embedding else 0)

        results = self._store.query(
            self._embedding, [query_embedding],
            n_results=fetch_k,
            where={"tool_name": {"$in": sorted(allowed_tool_names)}},
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
        if self._reranker and len(tool_names) > limit:
            logger.info("retrieve: running reranker on %d candidates → top_n=%d", len(tool_names), limit)
            try:
                rerank_results = await self._reranker.rerank(query, documents, top_n=limit)
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

        candidates = tuple(
            ScoredCandidate(name, scores.get(name, 0.0), rank, query)
            for rank, name in enumerate(tool_names[:limit], 1)
            if name in allowed_tool_names
        )
        logger.info("retrieve: final %d tools: %s", len(candidates), {c.tool_name: c.score for c in candidates})
        return SemanticRetrievalResult(candidates)
