"""Load and chunk study documents (PDF / txt / md)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from config import CHUNK_OVERLAP, CHUNK_SIZE, SUPPORTED_EXTS

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    text: str
    source: str
    category: str
    chunk_id: str


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF page extract failed %s: %s", path.name, exc)
    return "\n".join(parts)


def load_document(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return _read_text_file(path)
    if ext == ".pdf":
        return _read_pdf(path)
    raise ValueError(f"Unsupported file type: {ext}")


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []

    # Prefer paragraph / sentence boundaries when possible
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in paragraphs:
        if len(para) <= chunk_size:
            if buf and len(buf) + 2 + len(para) > chunk_size:
                flush()
            buf = f"{buf}\n\n{para}".strip() if buf else para
            continue

        # Long paragraph: sliding window
        if buf:
            flush()
        start = 0
        while start < len(para):
            end = min(start + chunk_size, len(para))
            piece = para[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= len(para):
                break
            start = max(end - overlap, start + 1)

    flush()
    return chunks


def iter_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
                files.append(path)
    return files


def chunk_files(category: str, roots: list[Path]) -> list[Chunk]:
    out: list[Chunk] = []
    for path in iter_files(roots):
        try:
            raw = load_document(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load %s: %s", path, exc)
            continue
        pieces = split_text(raw)
        rel = path.name
        for i, piece in enumerate(pieces):
            out.append(
                Chunk(
                    text=piece,
                    source=rel,
                    category=category,
                    chunk_id=f"{category}:{rel}:{i}",
                )
            )
        logger.info("Chunked %s (%s) → %d chunks", path, category, len(pieces))
    return out
