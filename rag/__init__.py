"""Local RAG: ingest, embed, retrieve, answer."""

from rag.pipeline import answer_question, get_index, reindex_all, search

__all__ = ["answer_question", "get_index", "reindex_all", "search"]
