from __future__ import annotations

import json
from pathlib import Path

from spw import DEFAULT_DB_PATH, TEMPLATE_PATH
from spw.export import export_dataset


def build_html_bundle(db_path: str | Path = DEFAULT_DB_PATH, output_path: str | Path = Path("dist/spw.html")) -> Path:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise RuntimeError("template is missing __DATA__ placeholder")
    payload = json.dumps(export_dataset(db_path), separators=(",", ":"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__DATA__", payload), encoding="utf-8")
    return output
