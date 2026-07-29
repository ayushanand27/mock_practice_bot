"""Chroma vector store with local sentence-transformers embeddings."""

from __future__ import annotations

import logging
from typing import Any

from config import CHROMA_DIR, EMBEDDING_MODEL, TOP_K
from rag.chunking import Chunk

logger = logging.getLogger(__name__)

_collection = None
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model %s …", EMBEDDING_MODEL)
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        from chromadb.config import Settings

        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = client.get_or_create_collection(
            name="study_materials",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _get_embedder()
    vectors = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def reset_category(category: str) -> None:
    col = _get_collection()
    try:
        existing = col.get(where={"category": category}, include=[])
        ids = existing.get("ids") or []
        if ids:
            # Chroma delete in batches
            batch = 500
            for i in range(0, len(ids), batch):
                col.delete(ids=ids[i : i + batch])
            logger.info("Cleared %d vectors for category=%s", len(ids), category)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset_category(%s): %s", category, exc)


def upsert_chunks(chunks: list[Chunk]) -> int:
    if not chunks:
        return 0
    col = _get_collection()
    batch = 64
    total = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        ids = [c.chunk_id for c in part]
        docs = [c.text for c in part]
        metas: list[dict[str, Any]] = [
            {"source": c.source, "category": c.category} for c in part
        ]
        embeddings = embed_texts(docs)
        col.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        total += len(part)
    return total


def query(category: str, question: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
    col = _get_collection()
    if col.count() == 0:
        return []
    q_emb = embed_texts([question])[0]
    result = col.query(
        query_embeddings=[q_emb],
        n_results=min(top_k, max(col.count(), 1)),
        where={"category": category},
        include=["documents", "metadatas", "distances"],
    )
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    out: list[dict[str, Any]] = []
    for doc, meta, dist in zip(docs, metas, dists):
        if not doc:
            continue
        out.append(
            {
                "text": doc,
                "source": (meta or {}).get("source", "?"),
                "distance": float(dist) if dist is not None else None,
            }
        )
    return out


def count(category: str | None = None) -> int:
    col = _get_collection()
    if category is None:
        return col.count()
    try:
        got = col.get(where={"category": category}, include=[])
        return len(got.get("ids") or [])
    except Exception:  # noqa: BLE001
        return 0
