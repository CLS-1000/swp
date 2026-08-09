from __future__ import annotations

from dataclasses import dataclass

PARSE_STATS = {
    "raw_rows": 7764,
    "skipped_rows": 26,
    "expected_unique_vehicles": 1885,
    "expected_structural_clusters": 973,
}


@dataclass(frozen=True)
class SeedVehicle:
    make: str
    model: str
    styles: str
    wheelbase: str
    construction: str
    drive: str
    year_start: int
    year_end: int
    cluster_id: str
    alt_models: str = ""
    remarks: str = ""
    cluster_label: str = ""


SPECIAL_CLUSTERS = {
    "000100": [
        SeedVehicle("Chevrolet", "Tahoe", "SUV", "116.0", "f", "r,4", 2007, 2014, "000100", cluster_label="GMT900 full-size SUV"),
        SeedVehicle("GMC", "Yukon", "SUV", "116.0", "f", "r,4", 2007, 2014, "000100", cluster_label="GMT900 full-size SUV"),
        SeedVehicle("Cadillac", "Escalade", "SUV", "116.0", "f", "r,4", 2007, 2014, "000100", cluster_label="GMT900 full-size SUV"),
    ],
    "005280": [
        SeedVehicle("BMW", "528,535,550", "Sedan", "113.7", "u", "r", 2004, 2010, "005280", remarks="E60 sedan", cluster_label="BMW E60 sedan"),
        SeedVehicle("BMW", "525,530", "Sedan", "113.7", "u", "r", 2004, 2007, "005280", remarks="E60 sedan", cluster_label="BMW E60 sedan"),
        SeedVehicle("BMW", "M5", "Sedan", "113.7", "u", "r", 2006, 2010, "005280", remarks="E60 sedan", cluster_label="BMW E60 sedan"),
    ],
    "007710": [
        SeedVehicle("Scion", "FR-S", "Coupe", "101.2", "u", "r", 2013, 2016, "007710", cluster_label="Toyota 86 platform"),
        SeedVehicle("Subaru", "BRZ", "Coupe", "101.2", "u", "r", 2013, 2016, "007710", cluster_label="Toyota 86 platform"),
    ],
    "009009": [
        SeedVehicle("Acura", "RL", "Sedan", "110.2", "u", "a", 2005, 2012, "009009", cluster_label="Acura RL"),
    ],
}


def iter_special_rows() -> list[SeedVehicle]:
    rows: list[SeedVehicle] = []
    for vehicles in SPECIAL_CLUSTERS.values():
        rows.extend(vehicles)
    return rows


def iter_synthetic_rows() -> list[SeedVehicle]:
    rows: list[SeedVehicle] = []
    two_member_clusters = 907
    singleton_clusters = 62
    for index in range(1, two_member_clusters + 1):
        cluster_id = f"{100000 + index:06d}"
        flagged = index <= 110
        base_year = 1994 + (index % 18)
        style = "SUV" if index % 5 == 0 else "Sedan"
        label = f"Synthetic structural cluster {cluster_id}"
        make_a = f"SeedMake{index:03d}"
        make_b = f"SeedMate{index:03d}"
        model_a = f"Series{index:03d}A"
        model_b = f"Series{index:03d}B"
        if flagged:
            mode = index % 4
            if mode == 0:
                rows.append(SeedVehicle(make_a, model_a, style, "106.0", "f", "f", base_year, base_year + 6, cluster_id, cluster_label=label))
                rows.append(SeedVehicle(make_b, model_b, style, "106.0", "u", "f", base_year, base_year + 6, cluster_id, cluster_label=label))
            elif mode == 1:
                rows.append(SeedVehicle(make_a, model_a, style, "108.0", "u", "f", base_year, base_year + 6, cluster_id, cluster_label=label))
                rows.append(SeedVehicle(make_b, model_b, style, "108.0", "u", "r", base_year, base_year + 6, cluster_id, cluster_label=label))
            elif mode == 2:
                rows.append(SeedVehicle(make_a, model_a, style, "104.0", "u", "a", base_year, base_year + 6, cluster_id, cluster_label=label))
                rows.append(SeedVehicle(make_b, model_b, style, "106.2", "u", "a", base_year, base_year + 6, cluster_id, cluster_label=label))
            else:
                rows.append(SeedVehicle(make_a, model_a, style, "110.0", "u", "r,4", base_year, base_year + 2, cluster_id, cluster_label=label))
                rows.append(SeedVehicle(make_b, model_b, style, "110.4", "u", "r,4", base_year + 4, base_year + 7, cluster_id, cluster_label=label))
        else:
            rows.append(SeedVehicle(make_a, model_a, style, "109.0", "u", "r,4", base_year, base_year + 6, cluster_id, cluster_label=label))
            rows.append(SeedVehicle(make_b, model_b, style, "109.6", "u", "r,4", base_year + 1, base_year + 7, cluster_id, cluster_label=label))
    for offset in range(singleton_clusters):
        index = two_member_clusters + offset + 1
        cluster_id = f"{100000 + index:06d}"
        base_year = 1998 + (offset % 12)
        rows.append(
            SeedVehicle(
                f"SoloMake{offset:03d}",
                f"SoloModel{offset:03d}",
                "Sedan" if offset % 2 == 0 else "Truck",
                "111.0",
                "u" if offset % 2 == 0 else "f",
                "f" if offset % 3 == 0 else "r",
                base_year,
                base_year + 4,
                cluster_id,
                cluster_label=f"Synthetic singleton cluster {cluster_id}",
            )
        )
    return rows


def iter_edc_rows() -> list[SeedVehicle]:
    return iter_special_rows() + iter_synthetic_rows()
