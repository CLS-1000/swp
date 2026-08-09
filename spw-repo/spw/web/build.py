"""Build dist/spw.html by injecting dataset JSON into template."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spw.export import to_json


def build(db_path: Path | None = None, template: Path | None = None, out: Path | None = None) -> Path:
    db_path = db_path or ROOT / "clone_clusters.db"
    template = template or ROOT / "web" / "template.html"
    out = out or ROOT / "dist" / "spw.html"
    out.parent.mkdir(exist_ok=True)

    data = to_json(db_path)
    html = template.read_text(encoding="utf-8")
    html = html.replace("__DATA__", data)
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    p = build()
    print(f"Built {p} ({p.stat().st_size / 1024:.0f} KB)")
