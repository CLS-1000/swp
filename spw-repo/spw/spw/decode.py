"""NHTSA vPIC VIN decoder — free API, no auth required."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
TIMEOUT = 10.0


@dataclass
class VehicleRecord:
    year: str
    make: str
    model: str
    trim: str
    body: str
    engine: str
    cylinders: str
    drive: str
    fuel: str
    plant: str
    error: str

    @property
    def vehicle_string(self) -> str:
        parts = [self.year, self.make, self.model]
        if self.trim:
            parts.append(self.trim)
        return " ".join(filter(None, parts))

    @property
    def engine_string(self) -> str:
        parts = []
        if self.engine:
            # displacement may be separate
            parts.append(self.engine)
        if self.cylinders:
            parts.append(f"{self.cylinders}-cyl")
        if self.fuel:
            parts.append(self.fuel)
        return " ".join(parts)


def decode(vin: str) -> VehicleRecord:
    """Decode a 17-character VIN via NHTSA vPIC.

    Raises httpx.HTTPError on network failure.
    Raises ValueError on invalid VIN or empty decode.
    """
    vin = vin.strip().upper()
    if len(vin) != 17:
        raise ValueError(f"VIN must be 17 characters, got {len(vin)}")

    r = httpx.get(VPIC_URL.format(vin=vin), timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json().get("Results", [{}])[0]

    if not d.get("Make") and not d.get("Model"):
        raise ValueError(f"No decode returned for VIN {vin} — check digits.")

    def _cap(s: str) -> str:
        return s.lower().replace("\\b\\w", lambda m: m.group().upper()) if s else ""

    make = d.get("Make", "")
    if make:
        make = make.strip().title()

    disp = f"{d.get('DisplacementL', '')}L" if d.get("DisplacementL") else ""
    engine_parts = [disp, d.get("EngineModel", "")]
    engine = " ".join(filter(None, engine_parts))

    plant_parts = [d.get("PlantCity", ""), d.get("PlantCountry", "")]
    plant = ", ".join(filter(None, plant_parts))

    trim_parts = [d.get("Trim", ""), d.get("DriveType", "")]
    trim = " · ".join(filter(None, trim_parts))

    return VehicleRecord(
        year=d.get("ModelYear", ""),
        make=make,
        model=d.get("Model", ""),
        trim=trim,
        body=d.get("BodyClass", ""),
        engine=engine,
        cylinders=d.get("EngineCylinders", ""),
        drive=d.get("DriveType", ""),
        fuel=d.get("FuelTypePrimary", ""),
        plant=plant,
        error=d.get("ErrorText", ""),
    )


def format_decode(rec: VehicleRecord) -> str:
    """Format decoded vehicle as human-readable text."""
    lines = [
        f"Vehicle:     {rec.vehicle_string}",
        f"Body:        {rec.body}",
        f"Engine:      {rec.engine}",
        f"Cylinders:   {rec.cylinders}",
        f"Drive:       {rec.drive}",
        f"Fuel:        {rec.fuel}",
        f"Plant:       {rec.plant}",
    ]
    if rec.error and not rec.error.startswith("0"):
        lines.append(f"vPIC Notes:  {rec.error}")
    return "\n".join(lines)
