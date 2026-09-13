from __future__ import annotations

from datetime import datetime
import hmac
import json
import math
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from urllib.error import HTTPError, URLError

from app.config import Settings
from app.db import Database, dump_json, now_iso, site_from_row
from app.imports import ImportPackage, ImportRecord, validate_import_package
from app.routes import RouteResult, build_gpx, fetch_openrouteservice


SITE_STATUSES = (
    "candidate",
    "approximate",
    "likely",
    "trusted",
    "field-verified",
    "destroyed-or-filled",
    "rejected",
)
SITE_ACCESSES = (
    "public",
    "private",
    "restricted",
    "unknown",
    "permission_required",
    "dangerous",
    "unsafe",
)
LOCATION_BASES = (
    "explicit_coordinate",
    "address",
    "map_reference",
    "landmark_description",
    "llm_inference",
)


class SitePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=500)
    site_kind: str | None = Field(default=None, min_length=1, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    precision: Literal["exact", "approximate", "unknown"] | None = None
    uncertainty_m: float | None = Field(default=None, ge=0)
    location_basis: Literal[
        "explicit_coordinate",
        "address",
        "map_reference",
        "landmark_description",
        "llm_inference",
    ] | None = None
    status: Literal[
        "candidate",
        "approximate",
        "likely",
        "trusted",
        "field-verified",
        "destroyed-or-filled",
        "rejected",
    ] | None = None
    access: Literal[
        "public",
        "private",
        "restricted",
        "unknown",
        "permission_required",
        "dangerous",
        "unsafe",
    ] | None = None
    condition: str | None = Field(default=None, max_length=1000)
    warnings: list[str] | None = None
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "SitePatch":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be edited together")
        for field in ("name", "site_kind", "precision", "location_basis", "status", "access", "warnings"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "reject", "restore", "merge"]
    target_site_id: int | None = Field(default=None, gt=0)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Trondheim field route", min_length=1, max_length=200)
    start: RoutePoint
    waypoints: list[RoutePoint] = Field(min_length=1, max_length=20)


def _validation_detail(error: ValidationError) -> list[dict[str, object]]:
    return json.loads(error.json())


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(first[1]), math.radians(first[0])
    lat2, lon2 = math.radians(second[1]), math.radians(second[0])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _source_rows(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT sources.url, sources.title, sources.source_type, sources.excerpt,
               sources.published_at, sources.accessed_at, evidence.role
        FROM evidence
        JOIN sources ON sources.id = evidence.source_id
        WHERE evidence.site_id = ?
        ORDER BY sources.id
        """,
        (site_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _site_detail(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["sources"] = _source_rows(connection, int(row["id"]))
    return site


def _existing_site(connection: sqlite3.Connection, external_key: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM sites WHERE external_key = ?",
        (external_key,),
    ).fetchone()


def _duplicate_warnings(
    connection: sqlite3.Connection, record: ImportRecord
) -> list[str]:
    geometry = record.geometry
    warnings = list(record.warnings)
    rows = connection.execute(
        """
        SELECT external_key, name, site_kind, latitude, longitude
        FROM sites
        WHERE merged_into_id IS NULL AND external_key <> ?
        """,
        (record.external_key,),
    ).fetchall()
    # ponytail: bounded O(n^2) scan is enough for the private v1 catalogue; add a spatial index at scale.
    for row in rows:
        if row["name"].casefold() != record.name.casefold():
            continue
        if row["site_kind"].casefold() != record.site_kind.casefold():
            continue
        if geometry is None or row["latitude"] is None or row["longitude"] is None:
            warnings.append(f"possible duplicate of {row['external_key']}")
            continue
        distance = _distance_m(
            (float(geometry.longitude), float(geometry.latitude)),
            (float(row["longitude"]), float(row["latitude"])),
        )
        if distance <= 100:
            warnings.append(
                f"possible duplicate of {row['external_key']} ({round(distance)} m away)"
            )
    return list(dict.fromkeys(warnings))


def _preview_package(
    connection: sqlite3.Connection, package: ImportPackage
) -> dict[str, object]:
    preview_records: list[dict[str, object]] = []
    counts = {"new": 0, "update_candidate": 0, "preserve_trusted": 0, "warnings": 0}
    for record in package.records:
        existing = _existing_site(connection, record.external_key)
        warnings = _duplicate_warnings(connection, record)
        if existing is None:
            action = "new"
        elif existing["status"] == "candidate":
            action = "update_candidate"
        else:
            action = "preserve_trusted"
        counts[action] += 1
        counts["warnings"] += len(warnings)
        preview_records.append(
            {
                "external_key": record.external_key,
                "name": record.name,
                "action": action,
                "existing_site_id": existing["id"] if existing else None,
                "warnings": warnings,
            }
        )
    return {
        "batch_id": package.batch_id,
        "schema_version": package.schema_version,
        "records": preview_records,
        "summary": {"total": len(package.records), **counts},
    }


def _site_values(record: ImportRecord, warnings: list[str]) -> tuple[object, ...]:
    latitude = record.geometry.latitude if record.geometry else None
    longitude = record.geometry.longitude if record.geometry else None
    return (
        record.external_key,
        record.name,
        record.site_kind,
        latitude,
        longitude,
        record.precision,
        record.uncertainty_m,
        record.location_basis,
        "candidate",
        record.access,
        record.condition,
        dump_json(warnings),
        record.short_rationale,
        record.observed_location_text,
    )


def _upsert_source(
    connection: sqlite3.Connection, source: object, timestamp: str
) -> int:
    connection.execute(
        """
        INSERT INTO sources
            (url, title, source_type, excerpt, published_at, accessed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = excluded.title,
            source_type = excluded.source_type,
            excerpt = excluded.excerpt,
            published_at = excluded.published_at,
            accessed_at = excluded.accessed_at,
            updated_at = excluded.updated_at
        """,
        (
            str(source.url),
            source.title,
            source.source_type,
            source.excerpt,
            source.publication_date.isoformat() if source.publication_date else None,
            source.access_date.isoformat() if source.access_date else None,
            timestamp,
            timestamp,
        ),
    )
    return int(
        connection.execute("SELECT id FROM sources WHERE url = ?", (str(source.url),)).fetchone()[0]
    )


def _commit_package(database: Database, package: ImportPackage) -> dict[str, object]:
    timestamp = now_iso()
    created = updated = preserved = evidence_attached = 0
    with database.connect() as connection:
        existing_batch = connection.execute(
            "SELECT committed_at FROM import_batches WHERE batch_id = ?",
            (package.batch_id,),
        ).fetchone()
        if existing_batch and existing_batch["committed_at"]:
            return {
                "batch_id": package.batch_id,
                "created": 0,
                "updated": 0,
                "preserved": 0,
                "evidence_attached": 0,
                "idempotent": True,
            }
        if existing_batch:
            raise HTTPException(409, "import batch is already in progress")

        connection.execute(
            """
            INSERT INTO import_batches
                (batch_id, schema_version, generated_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (package.batch_id, package.schema_version, package.generated_at.isoformat(), timestamp),
        )
        for record in package.records:
            warnings = _duplicate_warnings(connection, record)
            existing = _existing_site(connection, record.external_key)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sites
                        (external_key, name, site_kind, latitude, longitude, precision,
                         uncertainty_m, location_basis, status, access, condition,
                         warnings_json, short_rationale, observed_location_text,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*_site_values(record, warnings), timestamp, timestamp),
                )
                site_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                action = "created"
                created += 1
            elif existing["status"] == "candidate":
                connection.execute(
                    """
                    UPDATE sites SET name = ?, site_kind = ?, latitude = ?, longitude = ?,
                        precision = ?, uncertainty_m = ?, location_basis = ?, status = ?,
                        access = ?, condition = ?, warnings_json = ?, short_rationale = ?,
                        observed_location_text = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        record.name,
                        record.site_kind,
                        record.geometry.latitude if record.geometry else None,
                        record.geometry.longitude if record.geometry else None,
                        record.precision,
                        record.uncertainty_m,
                        record.location_basis,
                        "candidate",
                        record.access,
                        record.condition,
                        dump_json(warnings),
                        record.short_rationale,
                        record.observed_location_text,
                        timestamp,
                        existing["id"],
                    ),
                )
                site_id = int(existing["id"])
                action = "updated_candidate"
                updated += 1
            else:
                site_id = int(existing["id"])
                action = "preserved_reviewed"
                preserved += 1

            for source in record.sources:
                source_id = _upsert_source(connection, source, timestamp)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence
                        (site_id, source_id, role, created_at)
                    VALUES (?, ?, 'source', ?)
                    """,
                    (site_id, source_id, timestamp),
                )
                evidence_attached += cursor.rowcount

            payload_json = dump_json(record.model_dump(mode="json"))
            connection.execute(
                """
                INSERT INTO import_records
                    (batch_id, external_key, site_id, action, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (package.batch_id, record.external_key, site_id, action, payload_json, timestamp),
            )
            connection.execute(
                """
                INSERT INTO site_events (site_id, event_type, payload_json, created_at)
                VALUES (?, 'import', ?, ?)
                """,
                (site_id, payload_json, timestamp),
            )
        connection.execute(
            "UPDATE import_batches SET committed_at = ? WHERE batch_id = ?",
            (timestamp, package.batch_id),
        )
    return {
        "batch_id": package.batch_id,
        "created": created,
        "updated": updated,
        "preserved": preserved,
        "evidence_attached": evidence_attached,
        "idempotent": False,
    }


def _require_admin(settings: Settings, authorization: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(503, "admin authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(401, "invalid bearer token")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="Bunkerkartet", version=settings.app_version)
    app.state.settings = settings
    app.state.database = database

    def admin_guard(authorization: str | None = Header(default=None)) -> None:
        _require_admin(settings, authorization)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            raise HTTPException(503, "database unavailable")
        return {"status": "healthy", "version": settings.app_version, "database": "ready"}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": settings.app_version}

    @app.get("/api/imports/schema", dependencies=[Depends(admin_guard)])
    def import_schema() -> dict[str, object]:
        return ImportPackage.model_json_schema()

    @app.post("/api/admin/imports/preview", dependencies=[Depends(admin_guard)])
    def preview_import(payload: dict[str, object]) -> dict[str, object]:
        try:
            package = validate_import_package(payload)
        except ValidationError as error:
            raise HTTPException(422, detail=_validation_detail(error))
        with database.connect() as connection:
            return _preview_package(connection, package)

    @app.post("/api/admin/imports/commit", dependencies=[Depends(admin_guard)])
    def commit_import(payload: dict[str, object]) -> dict[str, object]:
        try:
            package = validate_import_package(payload)
        except ValidationError as error:
            raise HTTPException(422, detail=_validation_detail(error))
        return _commit_package(database, package)

    @app.get("/api/sites", dependencies=[Depends(admin_guard)])
    def list_sites(
        status: str | None = Query(default=None),
        site_kind: str | None = Query(default=None),
        include_rejected: bool = Query(default=False),
    ) -> list[dict[str, object]]:
        conditions = ["merged_into_id IS NULL"]
        values: list[object] = []
        if status:
            if status not in SITE_STATUSES:
                raise HTTPException(400, "invalid site status")
            conditions.append("status = ?")
            values.append(status)
        elif not include_rejected:
            conditions.append("status <> 'rejected'")
        if site_kind:
            conditions.append("site_kind = ?")
            values.append(site_kind)
        query = f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY name, id"
        with database.connect() as connection:
            return [site_from_row(row) for row in connection.execute(query, values).fetchall()]

    @app.get("/api/review/candidates", dependencies=[Depends(admin_guard)])
    def candidates() -> list[dict[str, object]]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sites WHERE status = 'candidate' AND merged_into_id IS NULL ORDER BY id"
            ).fetchall()
            return [_site_detail(connection, row) for row in rows]

    @app.get("/api/sites/{site_id}", dependencies=[Depends(admin_guard)])
    def get_site(site_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            return _site_detail(connection, row)

    @app.patch("/api/sites/{site_id}", dependencies=[Depends(admin_guard)])
    def edit_site(site_id: int, patch: SitePatch) -> dict[str, object]:
        values = patch.model_dump(exclude_unset=True)
        if "name" in values and values["name"] is not None and "snublestein" in values["name"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if "site_kind" in values and values["site_kind"] is not None and "snublestein" in values["site_kind"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if not values:
            raise HTTPException(400, "no site fields supplied")
        if "warnings" in values:
            values["warnings_json"] = dump_json(values.pop("warnings"))
        assignments = ", ".join(f"{field} = ?" for field in values)
        values["updated_at"] = now_iso()
        params = [*values.values(), site_id]
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            connection.execute(
                f"UPDATE sites SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'edit', ?, ?)",
                (site_id, dump_json(values), now_iso()),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return _site_detail(connection, updated)

    @app.post("/api/sites/{site_id}/review", dependencies=[Depends(admin_guard)])
    def review_site(site_id: int, request: ReviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if request.action == "merge":
                if request.target_site_id is None or request.target_site_id == site_id:
                    raise HTTPException(400, "merge requires a different target site")
                target = connection.execute(
                    "SELECT id FROM sites WHERE id = ? AND id <> ? AND merged_into_id IS NULL",
                    (request.target_site_id, site_id),
                ).fetchone()
                if target is None:
                    raise HTTPException(404, "merge target not found")
                connection.execute(
                    "UPDATE sites SET merged_into_id = ?, status = 'rejected', updated_at = ? WHERE id = ?",
                    (request.target_site_id, now_iso(), site_id),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (site_id, source_id, role, created_at)
                    SELECT ?, source_id, role, created_at FROM evidence WHERE site_id = ?
                    """,
                    (request.target_site_id, site_id),
                )
                connection.execute("DELETE FROM evidence WHERE site_id = ?", (site_id,))
                status = "merged"
            else:
                status = {
                    "accept": "likely",
                    "reject": "rejected",
                    "restore": "candidate",
                }[request.action]
                connection.execute(
                    "UPDATE sites SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now_iso(), site_id),
                )
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'review', ?, ?)",
                (site_id, dump_json(request.model_dump()), now_iso()),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": status, "site": _site_detail(connection, updated)}

    @app.post("/api/routes", dependencies=[Depends(admin_guard)])
    def create_route(request: RouteRequest) -> dict[str, object]:
        if not settings.ors_api_key:
            raise HTTPException(503, "routing is not configured")
        coordinates = [(request.start.lon, request.start.lat)] + [
            (point.lon, point.lat) for point in request.waypoints
        ]
        try:
            route: RouteResult = fetch_openrouteservice(settings.ors_api_key, coordinates)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(502, f"routing provider error: {error}")
        warnings = ["A route does not grant permission to enter land or structures."]
        if _distance_m(coordinates[0], route.coordinates[0]) > 50 or _distance_m(
            coordinates[-1], route.coordinates[-1]
        ) > 50:
            warnings.append("The provider snapped a route endpoint; verify the approach on site.")
        gpx = build_gpx(request.name, route.coordinates)
        timestamp = now_iso()
        with database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO route_plans
                    (name, start_json, waypoints_json, distance_m, duration_s,
                     geometry_json, gpx_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.name,
                    dump_json(request.start.model_dump()),
                    dump_json([point.model_dump() for point in request.waypoints]),
                    route.distance_m,
                    route.duration_s,
                    dump_json(route.coordinates),
                    gpx,
                    timestamp,
                ),
            )
            route_id = cursor.lastrowid
        return {
            "id": route_id,
            "name": request.name,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "geometry": {"type": "LineString", "coordinates": route.coordinates},
            "gpx": gpx,
            "warnings": warnings,
        }

    @app.get("/api/routes/{route_id}", dependencies=[Depends(admin_guard)])
    def get_route(route_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM route_plans WHERE id = ?", (route_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "route not found")
            return {
                "id": row["id"],
                "name": row["name"],
                "start": json.loads(row["start_json"]),
                "waypoints": json.loads(row["waypoints_json"]),
                "distance_m": row["distance_m"],
                "duration_s": row["duration_s"],
                "geometry": {"type": "LineString", "coordinates": json.loads(row["geometry_json"])},
                "gpx": row["gpx_text"],
            }

    return app


app = create_app()
