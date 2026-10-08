"""Local chat server: stdlib http.server on localhost only. No framework."""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from spw import REPO_ROOT
from spw.chat import ChatSession
from spw.gates.pack import load_packs
from spw.llm import LLM, default_llm

PAGE = REPO_ROOT / "web" / "chat.html"
MAX_BODY = 16 * 1024
MAX_SESSIONS = 200


def make_server(
    kb_path: str | Path,
    packs_dir: str | Path,
    diag_db: str | Path | None,
    port: int = 8765,
    llm: LLM | None = None,
) -> ThreadingHTTPServer:
    packs = load_packs(packs_dir)
    sessions: dict[str, ChatSession] = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "spw-chat"

        def log_message(self, fmt, *args):  # quiet
            return

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("localhost", "127.0.0.1", "::1")  # blocks DNS-rebinding pages

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/health":
                return self._json(200, {"ok": True, "mode": "llm" if llm else "offline", "packs": [p.id for p in packs]})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if self.path != "/api/chat":
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                return self._json(413, {"error": "body size"})
            try:
                payload = json.loads(self.rfile.read(length))
                message = str(payload["message"])
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "need JSON {message, session?}"})
            with lock:
                sid = payload.get("session")
                if sid not in sessions:
                    if len(sessions) >= MAX_SESSIONS:
                        sessions.pop(next(iter(sessions)))
                    sid = uuid.uuid4().hex[:12]
                    sessions[sid] = ChatSession(packs, kb_path, llm=llm, diag_db=diag_db, session_id=sid)
                turn = sessions[sid].turn(message)
            self._json(200, {"session": sid, "reply": turn.reply, "commit": turn.commit, "safeguard": turn.safeguard, "mode": sessions[sid].mode})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(kb: str | Path, packs: str | Path, diag_db: str | Path, port: int, offline: bool = False) -> None:
    llm = None if offline else default_llm()
    server = make_server(kb, packs, diag_db, port, llm)
    print(f"spw chat on http://127.0.0.1:{server.server_address[1]}  ({'llm' if llm else 'offline'} mode). Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
