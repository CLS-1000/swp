from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
DEFAULT_DB_PATH = ASSETS_DIR / "clone_clusters.db"
CURATED_SQL_PATH = ASSETS_DIR / "curated.sql"
KB_PATH = ASSETS_DIR / "knowledge.db"
DIAG_DB_PATH = ASSETS_DIR / "diag.db"
PACKS_DIR = REPO_ROOT / "packs"
DIST_DIR = REPO_ROOT / "dist"
TEMPLATE_PATH = REPO_ROOT / "web" / "template.html"

__all__ = [
    "ASSETS_DIR",
    "CURATED_SQL_PATH",
    "DEFAULT_DB_PATH",
    "DIAG_DB_PATH",
    "DIST_DIR",
    "KB_PATH",
    "PACKS_DIR",
    "REPO_ROOT",
    "TEMPLATE_PATH",
]
