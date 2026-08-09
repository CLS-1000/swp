from pathlib import Path

import web.build as web_build


def test_build_reads_utf8_template(tmp_path, monkeypatch):
    template = tmp_path / "template.html"
    template.write_text("<html>café ☕</html>", encoding="utf-8")
    out = tmp_path / "dist" / "spw.html"

    monkeypatch.setattr(web_build, "to_json", lambda db_path: '{"ok": true}')

    result = web_build.build(
        db_path=tmp_path / "clone_clusters.db",
        template=template,
        out=out,
    )

    assert result == out
    assert out.exists()
    assert "café ☕" in out.read_text(encoding="utf-8")
