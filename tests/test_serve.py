from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from spw.ingest import ingest_dir
from spw.serve import make_server
from tests.helpers import build_docs

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def server(tmp_path):
    build_docs(tmp_path / "docs")
    ingest_dir(tmp_path / "docs", tmp_path / "kb.db")
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "synthetic.json").write_text((FIX / "pack_synthetic.json").read_text())
    srv = make_server(tmp_path / "kb.db", packs, tmp_path / "diag.db", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(url, body, headers=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url + "/api/chat", data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_health(server):
    with urllib.request.urlopen(server + "/", timeout=5) as r:
        html = r.read().decode()
    assert "Diagnostic Chat" in html and r.status == 200
    with urllib.request.urlopen(server + "/api/health", timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True, "mode": "offline", "packs": ["synthetic"]}


def test_chat_round_trip_keeps_session(server):
    code, a = post(server, {"message": "2003 Synthetic Widget cranks but won't fire"})
    assert code == 200 and a["commit"] == "DEV-BRANCH-HALT-NO-TELEMETRY" and "COMMIT:" in a["reply"]
    code, b = post(server, {"session": a["session"], "message": "yes"})
    assert b["session"] == a["session"] and b["commit"] == "DEV-BRANCH-GATE-DTCS-YES"
    code, c = post(server, {"session": "bogus", "message": "hi"})
    assert c["session"] != "bogus"


def test_rejects_bad_input_and_foreign_host(server):
    assert post(server, None, raw=b"not json")[0] == 400
    assert post(server, {"nope": 1})[0] == 400
    assert post(server, None, raw=b"x" * 20000)[0] == 413
    assert post(server, {"message": "hi"}, headers={"Host": "evil.example"})[0] == 403
