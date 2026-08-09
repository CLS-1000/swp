from __future__ import annotations

import re
from typing import Any

import httpx

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{11,17}$")


class DecodeError(RuntimeError):
    pass


def decode_vin(vin: str) -> dict[str, Any]:
    normalized = vin.strip().upper()
    if not VIN_RE.fullmatch(normalized):
        raise DecodeError("VIN must be 11-17 characters using standard VIN alphabet")
    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{normalized}?format=json"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DecodeError(f"Unable to reach NHTSA vPIC (offline or blocked): {exc}") from exc
    payload = response.json()
    results = payload.get("Results") or []
    if not results:
        raise DecodeError("NHTSA vPIC returned no results")
    row = results[0]
    year = row.get("ModelYear") or ""
    make = row.get("Make") or ""
    model = row.get("Model") or ""
    engine_parts = [row.get("EngineModel") or "", row.get("EngineCylinders") or "", row.get("DisplacementL") or ""]
    engine = " ".join(part for part in engine_parts if part).strip()
    return {
        "vin": normalized,
        "year": year,
        "make": make,
        "model": model,
        "trim": row.get("Trim") or "",
        "series": row.get("Series") or "",
        "engine": engine,
    }
