from __future__ import annotations

import json
from pathlib import Path

from spw import DEFAULT_DB_PATH, PACKS_DIR, REPO_ROOT, TEMPLATE_PATH
from spw.export import export_dataset


def build_html_bundle(db_path: str | Path = DEFAULT_DB_PATH, output_path: str | Path = Path("dist/spw.html")) -> Path:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise RuntimeError("template is missing __DATA__ placeholder")
    payload = json.dumps(export_dataset(db_path), separators=(",", ":"))
    from spw.gates.pack import load_packs

    packs = json.dumps([p.data for p in load_packs(PACKS_DIR)], separators=(",", ":"))
    gates_js = (REPO_ROOT / "web" / "gates.js").read_text(encoding="utf-8")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    html = template.replace("__GATES_JS__", gates_js).replace("__PACKS__", packs).replace("__DATA__", payload)
    output.write_text(html, encoding="utf-8")
    return output
