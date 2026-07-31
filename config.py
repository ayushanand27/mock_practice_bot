"""App paths and study categories."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MATERIALS_DIR = DATA_DIR / "materials"
UPLOADS_DIR = DATA_DIR / "uploads"
CHROMA_DIR = DATA_DIR / "chroma"
NOTES_PATH = DATA_DIR / "notes.json"

# category_id -> (display label, materials subfolder)
CATEGORIES: dict[str, tuple[str, str]] = {
    "placement": ("Placement (BTech CSE)", "placement"),
    "jee": ("JEE", "jee"),
    "neet": ("NEET", "neet"),
    "ncert_11": ("Class 11 (NCERT)", "ncert_11"),
    "ncert_12": ("Class 12 (NCERT)", "ncert_12"),
    "ssc_cgl": ("SSC CGL", "ssc_cgl"),
    "upsc": ("UPSC", "upsc"),
}

TEST_TYPES = (
    ("MCQ", "mcq"),
    ("MSQ (multi-select)", "msq"),
    ("Numerical", "numerical"),
    ("Theoretical", "theory"),
)

SUPPORTED_EXTS = {".pdf", ".txt", ".md"}

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
TOP_K = 4


def category_label(category_id: str) -> str:
    return CATEGORIES.get(category_id, (category_id, category_id))[0]


def materials_path(category_id: str) -> Path:
    folder = CATEGORIES[category_id][1]
    return MATERIALS_DIR / folder


def uploads_path(user_id: int, category_id: str) -> Path:
    folder = CATEGORIES[category_id][1]
    return UPLOADS_DIR / str(user_id) / folder
