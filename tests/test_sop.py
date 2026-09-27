from pathlib import Path
from types import SimpleNamespace

from spw.sop import (
    MAX_SIBLINGS,
    collect_batches,
    export_sops,
    load_targets,
    pending_targets,
    plan,
    request_params,
    run_sample,
    sop_custom_id,
    submit_batch,
)


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-sonnet-5",
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=400, output_tokens=3000),
    )


class FakeBatches:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def create(self, requests: list[dict]) -> SimpleNamespace:
        self.submitted = requests
        return SimpleNamespace(id="msgbatch_1", processing_status="in_progress")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(processing_status="ended", request_counts=SimpleNamespace(processing=0))

    def results(self, batch_id: str):
        first, second, *rest = self.submitted
        yield SimpleNamespace(custom_id=second["custom_id"], result=SimpleNamespace(type="succeeded", message=_message("### 1. SPECS", "max_tokens")))
        yield SimpleNamespace(custom_id=first["custom_id"], result=SimpleNamespace(type="succeeded", message=_message("### 1. SPECS")))
        for request in rest:
            yield SimpleNamespace(custom_id=request["custom_id"], result=SimpleNamespace(type="errored"))


class FakeClient:
    def __init__(self) -> None:
        self.messages = SimpleNamespace(create=lambda **params: _message("### 1. SPECS"), batches=FakeBatches())


def test_targets_cover_engine_and_platform_clusters() -> None:
    targets = load_targets()
    n52 = [t for t in targets if t.cluster_id == "J37-LOOP"]
    assert n52 and all(t.scope == "engine-loop" for t in n52)
    assert any(t.scope == "platform" for t in targets)
    assert all(len(t.vehicles) <= MAX_SIBLINGS for t in targets)
    assert len({t.custom_id for t in targets}) == len(targets)


def test_custom_id_is_stable_and_batch_safe() -> None:
    custom_id = sop_custom_id("VW-MLB-EVO", "Water pump replacement")
    assert custom_id == sop_custom_id("VW-MLB-EVO", "Water pump replacement")
    assert len(custom_id) <= 64 and custom_id.replace("-", "").replace("_", "").isalnum()


def test_request_params_embed_cluster_vehicles() -> None:
    target = load_targets(clusters={"K24W"})[0]
    params = request_params(target)
    prompt = params["messages"][0]["content"]
    assert "K24W" in prompt and target.job in prompt and target.vehicles[0] in prompt
    assert params["thinking"] == {"type": "adaptive"}


def test_plan_counts_requests() -> None:
    targets = load_targets(clusters={"K24W", "VW-MQB"})
    report = plan(targets)
    assert report["requests"] == len(targets)
    assert report["clusters"] == 2
    assert 0 < report["est_batch_cost_usd"] <= report["worst_case_batch_cost_usd"]


def test_sample_stores_ok_sops_and_skips_them_next_time(tmp_path: Path) -> None:
    db_path = tmp_path / "sop.db"
    targets = load_targets(clusters={"K24W"})[:2]
    report = run_sample(FakeClient(), targets, db_path)
    assert [r["status"] for r in report["results"]] == ["ok", "ok"]
    assert report["batch_cost_per_sop_usd"] < report["sync_cost_usd"]
    assert pending_targets(targets, db_path) == []


def test_batch_round_trip_keys_results_by_custom_id(tmp_path: Path) -> None:
    db_path = tmp_path / "sop.db"
    client = FakeClient()
    targets = load_targets(clusters={"K24W"})[:3]
    submitted = submit_batch(client, targets, db_path)
    assert submitted == {"batch_id": "msgbatch_1", "requests": 3, "status": "in_progress"}
    assert pending_targets(targets, db_path) == []

    [report] = collect_batches(client, db_path)
    assert report["counts"] == {"max_tokens": 1, "ok": 1, "errored": 1}
    assert collect_batches(client, db_path) == []
    # Truncated and errored SOPs become eligible again once their batch has ended.
    assert pending_targets(targets, db_path) == targets[1:]

    exported = export_sops(db_path, tmp_path / "dist")
    assert exported["sops"] == 1
    written = tmp_path / "dist" / "sops" / "k24w" / "engine-oil-and-filter-change.md"
    assert written.read_text(encoding="utf-8").startswith("# K24W: Engine oil and filter change")
