from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spw import DEFAULT_DB_PATH, DIST_DIR, REPO_ROOT

FLAT_CSV_PATH = REPO_ROOT / "vehicles_flat.csv"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 8000
# Planning assumption for output tokens per SOP (visible text + thinking); `spw sop sample` measures the real figure.
EST_OUTPUT_TOKENS = 3500
MAX_SIBLINGS = 8

# USD per 1M tokens (input, output), Anthropic first-party list prices as of 2026-06-24. Batch API bills 50% of these.
# Only models that take adaptive thinking + output_config.effort are listed.
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

ENGINE_JOBS = [
    "Engine oil and filter change",
    "Spark plug replacement",
    "Ignition coil replacement",
    "Serpentine/accessory belt and tensioner replacement",
    "Water pump replacement",
    "Thermostat and housing replacement",
    "Valve cover gasket replacement",
    "PCV valve/system service",
    "Timing belt or chain inspection and replacement",
    "Alternator replacement",
    "Starter motor replacement",
    "Coolant drain, flush and fill",
]

PLATFORM_JOBS = [
    "Front brake pads and rotors replacement",
    "Rear brake pads and rotors replacement",
    "Front strut/shock assembly replacement",
    "Front lower control arm replacement",
    "Front wheel bearing/hub replacement",
    "Outer tie rod end replacement",
    "Sway bar end link replacement",
    "Cabin air filter replacement",
    "12V battery replacement and reset procedure",
]

SCOPE_JOBS = {"engine-loop": ENGINE_JOBS, "platform": PLATFORM_JOBS}

SOP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sops(
    custom_id TEXT PRIMARY KEY,
    cluster_id TEXT,
    scope TEXT,
    job TEXT,
    model TEXT,
    status TEXT,
    content TEXT,
    input_tokens INT,
    output_tokens INT,
    cost_usd REAL,
    batch_id TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS sop_batches(
    batch_id TEXT PRIMARY KEY,
    model TEXT,
    request_count INT,
    status TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS sop_requests(
    custom_id TEXT,
    batch_id TEXT,
    cluster_id TEXT,
    scope TEXT,
    job TEXT,
    PRIMARY KEY (custom_id, batch_id)
);
"""


@dataclass(frozen=True)
class SopTarget:
    cluster_id: str
    scope: str
    job: str
    vehicles: tuple[str, ...]

    @property
    def custom_id(self) -> str:
        return sop_custom_id(self.cluster_id, self.job)


def sop_custom_id(cluster_id: str, job: str) -> str:
    digest = hashlib.sha256(f"{cluster_id}|{job}".encode()).hexdigest()[:10]
    return f"{slug(cluster_id)[:40]}_{digest}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def ensure_sop_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SOP_SCHEMA_SQL)


def _vehicle_label(row: dict[str, str]) -> str:
    label = f"{row['year_start']}-{row['year_end']} {row['make']} {row['model']}"
    return f"{label} ({row['note']})" if row["note"] else label


def load_targets(
    csv_path: str | Path = FLAT_CSV_PATH,
    scopes: tuple[str, ...] = ("engine-loop", "platform"),
    clusters: set[str] | None = None,
) -> list[SopTarget]:
    members: dict[tuple[str, str], list[str]] = defaultdict(list)
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["scope"] not in scopes or not row["cluster_id"]:
                continue
            if clusters and row["cluster_id"] not in clusters:
                continue
            label = _vehicle_label(row)
            key = (row["scope"], row["cluster_id"])
            if label not in members[key]:
                members[key].append(label)
    targets = []
    for (scope, cluster_id), vehicles in sorted(members.items()):
        for job in SCOPE_JOBS[scope]:
            targets.append(SopTarget(cluster_id, scope, job, tuple(vehicles[:MAX_SIBLINGS])))
    return targets


def sop_prompt(target: SopTarget) -> str:
    kind = "engine family" if target.scope == "engine-loop" else "chassis platform"
    vehicles = "\n".join(f"- {vehicle}" for vehicle in target.vehicles)
    # Section structure mirrors sopPrompt() in web/template.html so renderMarkdown() handles both.
    return f"""You are an expert, highly meticulous Master Automotive Technician specializing in
home-garage DIY repair diagnostics and execution. Provide a comprehensive, highly
scannable, safety-focused Standard Operating Procedure (SOP) for the repair job below.
It covers every vehicle in the {kind} group listed. Where a spec, part or step differs
between listed vehicles, say which vehicle it applies to. If you are not confident in a
torque value or capacity, write "verify in factory service manual" instead of guessing.
Structure your response exactly into:

### 1. SPECIFICATIONS & CAPACITIES
Markdown table: fluid capacities, torque specs (ft-lbs and in-lbs), part numbers/types.

### 2. REQUIRED TOOLKIT & SUPPLIES
Two sub-bullets: "Standard Tools" and "Specialty Tools/Consumables".

### 3. THE S.O.P. (STEP-BY-STEP REPAIR)
Chronological numbered sequence. Group phases with #### subheadings. Bold key
actions, hardware sizes, components. Safety via blockquotes: "> CAUTION:" (danger)
or "> WARNING:" (high-risk failure points).

### 4. COMMON PITFALLS & PEER TIPS
3-5 platform/job-specific mistakes, shortcuts to avoid, or "while you are in there" items.

Respond with ONLY the SOP content. No preamble, no closing remarks.
---
{kind.upper()}: {target.cluster_id}
VEHICLES:
{vehicles}
JOB/REPAIR PROFILE: {target.job}"""


def request_params(target: SopTarget, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": sop_prompt(target)}],
    }


def cost_usd(model: str, input_tokens: int, output_tokens: int, batch: bool) -> float:
    price_in, price_out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    cost = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    return cost / 2 if batch else cost


def pending_targets(targets: list[SopTarget], db_path: str | Path) -> list[SopTarget]:
    """Targets with no stored SOP and no request in a batch that is still processing."""
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_sop_schema(conn)
        done = {row[0] for row in conn.execute("SELECT custom_id FROM sops WHERE status = 'ok'")}
        in_flight = {
            row[0]
            for row in conn.execute(
                """
                SELECT r.custom_id FROM sop_requests r
                JOIN sop_batches b ON b.batch_id = r.batch_id
                WHERE b.status != 'ended'
                """
            )
        }
    return [target for target in targets if target.custom_id not in done | in_flight]


def plan(targets: list[SopTarget], model: str = DEFAULT_MODEL) -> dict[str, Any]:
    input_tokens = sum(len(sop_prompt(target)) // 4 for target in targets)
    output_tokens = EST_OUTPUT_TOKENS * len(targets)
    worst_output = MAX_TOKENS * len(targets)
    by_scope: dict[str, int] = defaultdict(int)
    for target in targets:
        by_scope[target.scope] += 1
    return {
        "model": model,
        "requests": len(targets),
        "clusters": len({(t.scope, t.cluster_id) for t in targets}),
        "by_scope": dict(by_scope),
        "est_batch_cost_usd": round(cost_usd(model, input_tokens, output_tokens, batch=True), 2),
        "worst_case_batch_cost_usd": round(cost_usd(model, input_tokens, worst_output, batch=True), 2),
    }


def _message_text(message: Any) -> str:
    return "\n".join(block.text for block in message.content if block.type == "text").strip()


def _store(
    conn: sqlite3.Connection,
    custom_id: str,
    cluster_id: str,
    scope: str,
    job: str,
    message: Any,
    batch_id: str | None,
) -> dict[str, Any]:
    # Only a clean end_turn is publishable; truncated (max_tokens) or refused output is kept for inspection.
    status = "ok" if message.stop_reason == "end_turn" else message.stop_reason
    usage = message.usage
    cost = cost_usd(message.model, usage.input_tokens, usage.output_tokens, batch=batch_id is not None)
    conn.execute(
        """
        INSERT OR REPLACE INTO sops(custom_id, cluster_id, scope, job, model, status, content, input_tokens, output_tokens, cost_usd, batch_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            custom_id,
            cluster_id,
            scope,
            job,
            message.model,
            status,
            _message_text(message),
            usage.input_tokens,
            usage.output_tokens,
            cost,
            batch_id,
            datetime.now(UTC).isoformat(),
        ),
    )
    return {"custom_id": custom_id, "status": status, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": cost}


def run_sample(
    client: Any,
    targets: list[SopTarget],
    db_path: str | Path = DEFAULT_DB_PATH,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> dict[str, Any]:
    """Generate SOPs synchronously at full price; used to measure real per-SOP cost before a batch."""
    results = []
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_sop_schema(conn)
        for target in targets:
            message = client.messages.create(**request_params(target, model, effort))
            results.append(_store(conn, target.custom_id, target.cluster_id, target.scope, target.job, message, None))
            conn.commit()
    if not results:
        return {"results": []}
    mean_in = sum(r["input_tokens"] for r in results) / len(results)
    mean_out = sum(r["output_tokens"] for r in results) / len(results)
    return {
        "results": results,
        "mean_output_tokens": round(mean_out),
        "sync_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "batch_cost_per_sop_usd": round(cost_usd(model, int(mean_in), int(mean_out), batch=True), 4),
    }


def submit_batch(
    client: Any,
    targets: list[SopTarget],
    db_path: str | Path = DEFAULT_DB_PATH,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> dict[str, Any]:
    requests = [{"custom_id": target.custom_id, "params": request_params(target, model, effort)} for target in targets]
    batch = client.messages.batches.create(requests=requests)
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_sop_schema(conn)
        conn.execute(
            "INSERT INTO sop_batches(batch_id, model, request_count, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (batch.id, model, len(requests), batch.processing_status, datetime.now(UTC).isoformat()),
        )
        conn.executemany(
            "INSERT INTO sop_requests(custom_id, batch_id, cluster_id, scope, job) VALUES (?, ?, ?, ?, ?)",
            [(t.custom_id, batch.id, t.cluster_id, t.scope, t.job) for t in targets],
        )
        conn.commit()
    return {"batch_id": batch.id, "requests": len(requests), "status": batch.processing_status}


def collect_batches(client: Any, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    reports = []
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_sop_schema(conn)
        open_batches = [row[0] for row in conn.execute("SELECT batch_id FROM sop_batches WHERE status != 'ended'")]
        for batch_id in open_batches:
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status != "ended":
                reports.append({"batch_id": batch_id, "status": batch.processing_status, "processing": batch.request_counts.processing})
                continue
            requests = {
                row[0]: row[1:]
                for row in conn.execute("SELECT custom_id, cluster_id, scope, job FROM sop_requests WHERE batch_id = ?", (batch_id,))
            }
            counts: dict[str, int] = defaultdict(int)
            cost = 0.0
            # Results arrive in any order; key by custom_id.
            for result in client.messages.batches.results(batch_id):
                kind = result.result.type
                if kind != "succeeded" or result.custom_id not in requests:
                    counts[kind] += 1
                    continue
                cluster_id, scope, job = requests[result.custom_id]
                stored = _store(conn, result.custom_id, cluster_id, scope, job, result.result.message, batch_id)
                counts[stored["status"]] += 1
                cost += stored["cost_usd"]
            conn.execute("UPDATE sop_batches SET status = 'ended' WHERE batch_id = ?", (batch_id,))
            conn.commit()
            reports.append({"batch_id": batch_id, "status": "ended", "counts": dict(counts), "cost_usd": round(cost, 2)})
    return reports


def export_sops(db_path: str | Path = DEFAULT_DB_PATH, dist_dir: str | Path = DIST_DIR) -> dict[str, Any]:
    destination = Path(dist_dir) / "sops"
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_sop_schema(conn)
        rows = conn.execute(
            "SELECT cluster_id, scope, job, model, content, created_at FROM sops WHERE status = 'ok' ORDER BY scope, cluster_id, job"
        ).fetchall()
    index = []
    for cluster_id, scope, job, model, content, created_at in rows:
        relative = Path(slug(cluster_id)) / f"{slug(job)}.md"
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {cluster_id}: {job}\n\n{content}\n", encoding="utf-8")
        index.append({"cluster_id": cluster_id, "scope": scope, "job": job, "model": model, "created_at": created_at, "path": relative.as_posix()})
    destination.mkdir(parents=True, exist_ok=True)
    index_path = destination / "index.json"
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {"sops": len(index), "index": str(index_path)}
