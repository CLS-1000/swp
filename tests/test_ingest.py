"""G9: ingest."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("pandas")

from spw.ingest import ingest_dir, retrieve, stats
from spw.ingest.chunk import chunk_text
from tests.helpers import build_docs

REPO = Path(__file__).resolve().parent.parent


def test_fixture_counts(tmp_path):
    build_docs(tmp_path / "docs")
    kb = tmp_path / "kb.db"
    rep = ingest_dir(tmp_path / "docs", kb)
    # pdf: 2 pages/2 chunks; md: 3 sections; txt: 1; csv: 2 spec rows, 1 skipped row
    assert rep.files_ingested == 4
    assert rep.pages == 2 + 1 + 1
    assert rep.chunks == 2 + 3 + 1
    assert rep.spec_rows == 2
    assert {"file": "image.png", "reason": "unsupported type .png"} in rep.skipped
    assert stats(kb) == {"files": 4, "chunks": 6, "spec_rows": 2}


def test_reingest_is_noop_and_change_replaces(tmp_path):
    docs = tmp_path / "docs"
    build_docs(docs)
    kb = tmp_path / "kb.db"
    ingest_dir(docs, kb)
    before = stats(kb)
    again = ingest_dir(docs, kb)
    assert again.files_ingested == 0 and again.chunks == 0
    assert any(s["reason"] == "unchanged" for s in again.skipped)
    assert stats(kb) == before
    (docs / "notes.txt").write_text("Different synthetic note about a camshaft sensor.")
    changed = ingest_dir(docs, kb)
    assert changed.files_ingested == 1
    assert stats(kb)["chunks"] == before["chunks"]
    assert retrieve("camshaft", 3, kb) and not retrieve("crankshaft", 3, kb)


def test_retrieve_cites_and_ranks(tmp_path):
    build_docs(tmp_path / "docs")
    kb = tmp_path / "kb.db"
    ingest_dir(tmp_path / "docs", kb)
    hits = retrieve("ground strap", 3, kb)
    assert hits[0].cite == "manual.pdf p.2"
    hits = retrieve("synthetic bus resistor", 3, kb)
    assert hits[0].file == "sop.md" and hits[0].section == "CAN bus"
    assert retrieve("zzzqqq", 3, kb) == []
    assert retrieve('weird "quotes" AND (parens)', 3, kb) is not None


def test_chunking_size_and_overlap():
    text = "word " * 600
    chunks = chunk_text(text)
    assert len(chunks) > 3
    assert all(len(c) <= 800 for c in chunks)
    assert chunks[0][-50:].strip() in chunks[1]


def test_spec_rows_normalized(tmp_path):
    build_docs(tmp_path / "docs")
    kb = tmp_path / "kb.db"
    ingest_dir(tmp_path / "docs", kb)
    from spw.ingest.store import connect

    rows = connect(kb).execute("SELECT * FROM specs ORDER BY id").fetchall()
    assert rows[0]["spec_key"] == "synthmarelli.ecu_supply_koeo"
    assert (rows[0]["vmin"], rows[0]["vmax"], rows[0]["year_start"], rows[0]["year_end"]) == (1.11, 2.22, 2001, 2005)
    assert rows[1]["value"] == 3.33 and rows[1]["source_file"] == "specs.csv" and rows[1]["page"] == 8


BANNED = {"pandas", "pypdf", "openpyxl"}


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], node
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0], node


def test_runtime_never_imports_ingest_deps_at_module_level():
    for path in (REPO / "spw").rglob("*.py"):
        tree = ast.parse(path.read_text())
        top_level = {id(n) for n in tree.body}
        for name, node in _imports(tree):
            if name in BANNED:
                assert path.name == "readers.py", f"{path} imports {name}"
                assert id(node) not in top_level, f"{path} imports {name} at module level"


def test_runtime_imports_with_ingest_deps_blocked():
    code = (
        "import sys\n"
        + "".join(f"sys.modules[{n!r}] = None\n" for n in sorted(BANNED))
        + "import spw.cli, spw.ingest, spw.ingest.store\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, check=False)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr
