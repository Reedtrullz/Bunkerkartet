from __future__ import annotations

import os
from pathlib import Path

from app.config import Settings
from app.db import dump_json, now_iso
from app.main import create_app
from app.routes import RouteResult, build_gpx
import app.main as main_module


def _stub_route(api_key: str, coordinates: list[tuple[float, float]]) -> RouteResult:
    return RouteResult(
        distance_m=1200,
        duration_s=900,
        coordinates=[coordinates[0], coordinates[-1]],
    )


main_module.fetch_openrouteservice = _stub_route
settings = Settings(
    data_dir=Path(os.environ["BUNKERKARTET_DATA_DIR"]),
    admin_token="audit-only",
    ors_api_key="synthetic-ors-key",
    app_version="browser-test",
)
app = create_app(settings)

timestamp = now_iso()
route_coordinates = [(10.3951, 63.4305), (10.4, 63.435)]
gpx = build_gpx(
    "Synthetic saved route",
    route_coordinates,
    [(10.3951, 63.4305, "Route start"), (10.4, 63.435, "Synthetic site")],
)
with app.state.database.connect() as connection:
    connection.execute(
        """
        INSERT INTO sites
            (external_key, name, site_kind, latitude, longitude, precision,
             uncertainty_m, location_basis, status, access, confidence,
             warnings_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "browser:site",
            "Synthetic site",
            "bunker",
            63.435,
            10.4,
            "approximate",
            100,
            "map_reference",
            "candidate",
            "unknown",
            "medium",
            dump_json([]),
            timestamp,
            timestamp,
        ),
    )
    source_cursor = connection.execute(
        """
        INSERT INTO sources
            (url, title, source_type, excerpt, accessed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "https://example.com/browser-source",
            "Synthetic source",
            "test",
            "Synthetic excerpt",
            "2026-09-14",
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (?, ?, ?, ?)",
        (1, source_cursor.lastrowid, "source", timestamp),
    )
    connection.execute(
        """
        INSERT INTO sites
            (external_key, name, site_kind, latitude, longitude, precision,
             uncertainty_m, location_basis, status, access, confidence,
             warnings_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "browser:duplicate",
            "Synthetic duplicate",
            "bunker",
            63.435,
            10.4,
            "approximate",
            120,
            "map_reference",
            "candidate",
            "unknown",
            "low",
            dump_json([]),
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO route_plans
            (name, start_json, waypoints_json, distance_m, duration_s,
             geometry_json, gpx_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Synthetic saved route",
            dump_json({"lat": 63.4305, "lon": 10.3951}),
            dump_json([{"lat": 63.435, "lon": 10.4}]),
            1200,
            900,
            dump_json(route_coordinates),
            gpx,
            timestamp,
        ),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["BUNKERKARTET_PORT"]), log_level="warning")
