"""Index materials and answer study questions with Groq + retrieved context."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config import CATEGORIES, CHROMA_DIR, MATERIALS_DIR, UPLOADS_DIR, materials_path, uploads_path
from rag import chunking, store
from services import groq_service

logger = logging.getLogger(__name__)

_indexed = False
_META_PATH = CHROMA_DIR / ".index_meta.json"


def _category_roots(category: str, user_id: int | None = None) -> list[Path]:
    roots = [materials_path(category)]
    if user_id is not None:
        roots.append(uploads_path(user_id, category))
    # Also include all user uploads for shared indexing of that category
    uploads_root = UPLOADS_DIR
    if uploads_root.exists():
        folder = CATEGORIES[category][1]
        for user_dir in uploads_root.iterdir():
            if user_dir.is_dir():
                cat_dir = user_dir / folder
                if cat_dir.exists() and cat_dir not in roots:
                    roots.append(cat_dir)
    return roots


def _corpus_fingerprint() -> dict:
    """Cheap signature of materials + uploads so we rebuild when files change."""
    files: list[list] = []
    for root in (MATERIALS_DIR, UPLOADS_DIR):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".pdf", ".txt", ".md"}:
                try:
                    st = path.stat()
                    files.append([str(path.relative_to(root.parent)).replace("\\", "/"), int(st.st_mtime), int(st.st_size)])
                except OSError:
                    continue
    return {"files": files, "n": len(files)}


def _load_meta() -> dict | None:
    try:
        if _META_PATH.exists():
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _save_meta(fp: dict, summary: dict[str, dict[str, int]]) -> None:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fp, "summary": summary}
    _META_PATH.write_text(json.dumps(payload), encoding="utf-8")


def reindex_category(category: str) -> dict[str, int]:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")
    chunks = chunking.chunk_files(category, _category_roots(category))
    store.reset_category(category)
    n = store.upsert_chunks(chunks)
    logger.info("Reindexed %s: %d chunks", category, n)
    stats = {"chunks": n, "files": len({c.source for c in chunks})}
    # Keep fingerprint current so restart does not needlessly rebuild
    meta = _load_meta() or {}
    summary = dict(meta.get("summary") or {})
    summary[category] = stats
    _save_meta(_corpus_fingerprint(), summary)
    return stats


def reindex_all() -> dict[str, dict[str, int]]:
    global _indexed
    summary: dict[str, dict[str, int]] = {}
    for cat in CATEGORIES:
        summary[cat] = reindex_category(cat)
    _save_meta(_corpus_fingerprint(), summary)
    _indexed = True
    return summary


def ensure_indexed() -> None:
    global _indexed
    if _indexed:
        return
    fp = _corpus_fingerprint()
    meta = _load_meta()
    count = store.count()
    if count > 0 and meta and meta.get("fingerprint") == fp:
        _indexed = True
        logger.info("Using existing Chroma index (%d chunks)", count)
        return
    reason = "empty index" if count == 0 else "materials/uploads changed"
    logger.info("Rebuilding RAG index (%s)…", reason)
    reindex_all()


def get_index() -> None:
    """Warm embedder + collection (call on startup)."""
    ensure_indexed()


def search(category: str, question: str, top_k: int = 4) -> list[dict]:
    ensure_indexed()
    return store.query(category, question, top_k=top_k)


def answer_question(category: str, question: str) -> str:
    ensure_indexed()
    hits = store.query(category, question)
    if not hits:
        return (
            "I couldn't find relevant material for this category yet.\n"
            "Add PDFs/txt under data/materials/{category}/ or upload a file, then /reindex."
        )
    context_blocks = []
    sources: list[str] = []
    for h in hits:
        src = h["source"]
        if src not in sources:
            sources.append(src)
        context_blocks.append(f"[{src}]\n{h['text']}")
    context = "\n\n---\n\n".join(context_blocks)
    answer = groq_study_answer(category, question, context)
    cited = ", ".join(sources[:4])
    return f"{answer}\n\nSources: {cited}"


def groq_study_answer(category: str, question: str, context: str) -> str:
    from config import category_label

    label = category_label(category)
    system = (
        f"You are a clear, patient tutor for {label} exam preparation. "
        "Answer using ONLY the provided study materials. "
        "If the materials do not contain enough information, say so briefly and "
        "give the best partial answer you can from them. "
        "Use short paragraphs or bullet points. No fluff."
    )
    user = f"Study materials:\n{context}\n\nStudent question:\n{question}"
    return groq_service.chat(system, user, max_tokens=700, temperature=0.3)


def context_for_topic(category: str, topic_hint: str) -> str:
    hits = search(category, topic_hint, top_k=5)
    if not hits:
        return ""
    return "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
