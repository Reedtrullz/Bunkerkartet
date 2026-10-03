#!/usr/bin/env python3
"""Reproducible synthetic catalogue listing and observation batching probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import _observation_points, create_app


ADMIN = {"Authorization": "Bearer synthetic-catalogue-benchmark"}
SIZES = (100, 1000)
OBSERVATIONS_PER_SITE = 2
REPEATS = 3


def _seed(database: Database, site_count: int) -> None:
    with database.connect() as connection:
        for offset in range(site_count):
            site_id = offset + 1
            latitude = 63.0 + offset / 100_000
            longitude = 10.0 + offset / 100_000
            connection.execute(
                """INSERT INTO sites
                   (id,external_key,name,site_kind,latitude,longitude,precision,uncertainty_m,
                    location_basis,status,access,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'approximate',25,'map_reference','candidate','unknown',?,?)""",
                (site_id, f"benchmark:{site_id:04d}", f"Synthetic site {site_id:04d}",
                 "shelter", latitude, longitude, "2026-01-01", "2026-01-01"),
            )
            for observation_index in range(OBSERVATIONS_PER_SITE):
                observation_id = site_id * OBSERVATIONS_PER_SITE + observation_index
                connection.execute(
                    """INSERT INTO field_observations
                       (site_id,observed_at,outcome,note,latitude,longitude,point_role,
                        uncertainty_m,photo_urls_json,context_json,request_id,payload_hash,created_at)
                       VALUES (?,?,'found',?,?,?,?,?,'[]','{}',?,?,?)""",
                    (site_id, date(2026, 1, 1).isoformat(),
                     f"synthetic note {observation_id}", latitude + observation_index / 1_000_000,
                     longitude + observation_index / 1_000_000, "feature", 12.0,
                     f"benchmark-observation-{observation_id}", f"{observation_id:064x}",
                     "2026-01-01T12:00:00+00:00"),
                )


def _measure(database: Database, sql_probe: dict[str, int], operation: Callable[[], object]) -> tuple[object, dict[str, float | int]]:
    sql_probe["count"] = 0
    tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {"sql_statements": sql_probe["count"], "elapsed_ms": round(elapsed_ms, 3),
                    "peak_python_bytes": peak_bytes}


def _projection_before(database: Database) -> list[dict[str, object]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM sites WHERE merged_into_id IS NULL ORDER BY name,id"
        ).fetchall()
        return [{"site_id": int(row["id"]),
                 "observation_points": _observation_points(connection, int(row["id"]))}
                for row in rows]


def _projection_after(database: Database) -> list[dict[str, object]]:
    from app.catalogue_quality import batch_observation_points

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM sites WHERE merged_into_id IS NULL ORDER BY name,id"
        ).fetchall()
        site_ids = [int(row["id"]) for row in rows]
        points = batch_observation_points(connection, site_ids)
        return [{"site_id": site_id, "observation_points": points[site_id]}
                for site_id in site_ids]


def run_size(site_count: int, phase: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"catalogue-{site_count}-") as temporary:
        app = create_app(Settings(data_dir=Path(temporary),
                                  admin_token="synthetic-catalogue-benchmark"))
        with TestClient(app) as client:
            database = app.state.database
            _seed(database, site_count)
            sql_probe = {"count": 0}
            connect = database.connect

            def traced_connect():
                connection = connect()
                connection.set_trace_callback(lambda _statement: sql_probe.__setitem__(
                    "count", sql_probe["count"] + 1))
                return connection

            database.connect = traced_connect
            response, api_measurement = _measure(
                database, sql_probe,
                lambda: client.get("/api/sites", headers=ADMIN),
            )
            if response.status_code != 200:
                raise RuntimeError(f"synthetic catalogue list failed: {response.status_code}")
            api_bytes = len(response.content)

            before_results = []
            after_results = []
            if phase in {"before", "both"}:
                for _ in range(REPEATS):
                    payload, measurement = _measure(
                        database, sql_probe, lambda: _projection_before(database)
                    )
                    before_results.append((payload, measurement))
            if phase in {"after", "both"}:
                for _ in range(REPEATS):
                    payload, measurement = _measure(
                        database, sql_probe, lambda: _projection_after(database)
                    )
                    after_results.append((payload, measurement))

            if before_results and after_results:
                if before_results[-1][0] != after_results[-1][0]:
                    raise AssertionError("batched observation projection changed list points")

            result: dict[str, object] = {
                "site_count": site_count,
                "observations_per_site": OBSERVATIONS_PER_SITE,
                "api_list_current": {
                    **api_measurement, "payload_bytes": api_bytes,
                    "items": len(response.json()),
                },
            }
            for label, runs in (("observation_projection_before", before_results),
                                ("observation_projection_batched", after_results)):
                if not runs:
                    continue
                counts = [measurement["sql_statements"] - 1 for _, measurement in runs]
                times = [measurement["elapsed_ms"] for _, measurement in runs]
                peaks = [measurement["peak_python_bytes"] for _, measurement in runs]
                result[label] = {
                    "sql_statements_excluding_common_site_id_read": counts[-1],
                    "sql_statement_samples": counts,
                    "payload_bytes": len(json.dumps(runs[-1][0], sort_keys=True,
                                                     separators=(",", ":")).encode()),
                    "payload_sha256": hashlib.sha256(json.dumps(
                        runs[-1][0], sort_keys=True, separators=(",", ":")
                    ).encode()).hexdigest(),
                    "median_elapsed_ms": round(statistics.median(times), 3),
                    "max_peak_python_bytes": max(peaks),
                    "repeats": REPEATS,
                }
            result["scope"] = (
                "API measurements cover the current full GET /api/sites response (the current checkout "
                "calls the batched helper from that list controller). "
                "Before/after projection measurements cover the identical synthetic observation_points "
                "list component; they do not measure browser rendering or claim UI performance."
            )
            return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after", "both"), default="both",
                        help="which deterministic projection paths to measure")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES),
                        help="synthetic site counts (default: 100 1000)")
    args = parser.parse_args()
    if any(size <= 0 or size > 2000 for size in args.sizes):
        parser.error("sizes must be between 1 and 2000")
    output = {
        "fixture": "synthetic SQLite only",
        "phase": args.phase,
        "declared_target": "observation SQL reads <= ceil(sites/500)+ceil(observations/500); point payload unchanged",
        "results": [run_size(size, args.phase) for size in args.sizes],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
