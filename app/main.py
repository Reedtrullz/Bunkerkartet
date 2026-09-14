from __future__ import annotations

from datetime import date, datetime
import hmac
import json
import math
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator
from urllib.error import HTTPError, URLError

from app.config import Settings
from app.db import Database, dump_json, now_iso, observation_from_row, site_from_row
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
CONFIDENCE_LEVELS = ("high", "medium", "low", "unknown")
LOCATION_BASES = (
    "explicit_coordinate",
    "address",
    "map_reference",
    "landmark_description",
    "llm_inference",
)
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com 'unsafe-inline'; img-src 'self' data: blob: https://cache.kartverket.no; font-src 'self' data:; connect-src 'self'",
    "Permissions-Policy": "geolocation=(self)",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


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
    confidence: Literal["high", "medium", "low", "unknown"] | None = None
    condition: str | None = Field(default=None, max_length=1000)
    warnings: list[str] | None = None
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "SitePatch":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be edited together")
        for field in ("name", "site_kind", "precision", "location_basis", "status", "access", "confidence", "warnings"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class FieldObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: date
    outcome: Literal["found", "not_found", "inaccessible", "needs_follow_up"]
    note: str = Field(min_length=1, max_length=4000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    access_notes: str | None = Field(default=None, max_length=2000)
    photo_urls: list[HttpUrl] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "FieldObservation":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be recorded together")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "accept",
        "research",
        "field_verify",
        "confirm",
        "reject",
        "restore",
        "mark_approximate",
        "mark_destroyed",
        "merge",
    ]
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


def _observation_points(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT id, observed_at, outcome, latitude, longitude
        FROM field_observations
        WHERE site_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY observed_at DESC, id DESC
        """,
        (site_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _site_summary(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["observation_points"] = _observation_points(connection, int(row["id"]))
    return site


def _site_detail(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["sources"] = _source_rows(connection, int(row["id"]))
    observations = connection.execute(
        """
        SELECT id, site_id, observed_at, outcome, note, latitude, longitude,
               observed_location_text, access_notes, photo_urls_json, created_at
        FROM field_observations
        WHERE site_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (int(row["id"]),),
    ).fetchall()
    site["field_observations"] = [observation_from_row(item) for item in observations]
    site["observation_points"] = [
        {
            "id": observation["id"],
            "observed_at": observation["observed_at"],
            "outcome": observation["outcome"],
            "latitude": observation["latitude"],
            "longitude": observation["longitude"],
        }
        for observation in site["field_observations"]
        if observation["latitude"] is not None and observation["longitude"] is not None
    ]
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
        record.confidence,
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
                         uncertainty_m, location_basis, status, access, confidence, condition,
                         warnings_json, short_rationale, observed_location_text,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        access = ?, confidence = ?, condition = ?, warnings_json = ?, short_rationale = ?,
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
                        record.confidence,
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


def _route_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "start": json.loads(row["start_json"]),
        "waypoints": json.loads(row["waypoints_json"]),
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "created_at": row["created_at"],
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="Bunkerkartet", version=settings.app_version)
    app.state.settings = settings
    app.state.database = database

    @app.middleware("http")
    async def set_response_headers(request, call_next):
        response = await call_next(request)
        if request.url.path in {"/", "/static/app.js", "/static/styles.css"} or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def admin_guard(authorization: str | None = Header(default=None)) -> None:
        _require_admin(settings, authorization)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text()
        html = html.replace('/static/app.js"', f'/static/app.js?v={settings.app_version}"')
        return HTMLResponse(html)

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
        site_kind: str | None = Query(default=None, max_length=100),
        q: str | None = Query(default=None, max_length=200),
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
        if site_kind and site_kind.strip():
            conditions.append("lower(site_kind) LIKE lower(?)")
            values.append(f"%{site_kind.strip()}%")
        if q and q.strip():
            term = f"%{q.strip()}%"
            searchable_fields = ("name", "site_kind", "short_rationale", "observed_location_text", "condition")
            conditions.append(
                "(" + " OR ".join(
                    f"lower(COALESCE({field}, '')) LIKE lower(?)" for field in searchable_fields
                ) + " OR EXISTS ("
                "SELECT 1 FROM evidence JOIN sources ON sources.id = evidence.source_id "
                "WHERE evidence.site_id = sites.id AND ("
                "lower(COALESCE(sources.title, '')) LIKE lower(?) OR "
                "lower(COALESCE(sources.excerpt, '')) LIKE lower(?)"
                ")))"
            )
            values.extend([term] * (len(searchable_fields) + 2))
        query = f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY name, id"
        with database.connect() as connection:
            return [_site_summary(connection, row) for row in connection.execute(query, values).fetchall()]

    @app.get("/api/sites.geojson", dependencies=[Depends(admin_guard)])
    def export_sites_geojson() -> JSONResponse:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sites WHERE merged_into_id IS NULL AND status <> 'rejected' ORDER BY name, id"
            ).fetchall()
            features: list[dict[str, object]] = []
            for row in rows:
                site = _site_summary(connection, row)
                site_id = int(site["id"])
                geometry = None
                if site["latitude"] is not None and site["longitude"] is not None:
                    geometry = {
                        "type": "Point",
                        "coordinates": [site["longitude"], site["latitude"]],
                    }
                properties = {
                    key: value for key, value in site.items() if key != "observation_points"
                }
                properties["feature_type"] = "site"
                features.append(
                    {"type": "Feature", "id": site_id, "geometry": geometry, "properties": properties}
                )
                for observation in site["observation_points"]:
                    features.append(
                        {
                            "type": "Feature",
                            "id": f"observation-{observation['id']}",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [observation["longitude"], observation["latitude"]],
                            },
                            "properties": {
                                "feature_type": "field_observation",
                                "site_id": site_id,
                                "observed_at": observation["observed_at"],
                                "outcome": observation["outcome"],
                            },
                        }
                    )
        return JSONResponse(
            {"type": "FeatureCollection", "features": features},
            media_type="application/geo+json",
        )

    @app.get("/api/field-priority", dependencies=[Depends(admin_guard)])
    def field_priority(limit: int = Query(default=12, ge=1, le=50)) -> list[dict[str, object]]:
        # ponytail: public-only shortlist avoids inferring permission from unknown access.
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sites
                WHERE merged_into_id IS NULL
                  AND status IN ('candidate', 'likely')
                  AND access = 'public'
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY
                  CASE COALESCE(confidence, 'unknown')
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low' THEN 2
                    ELSE 3
                  END,
                  CASE WHEN uncertainty_m IS NULL THEN 1 ELSE 0 END,
                  uncertainty_m,
                  name,
                  id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [_site_detail(connection, row) for row in rows]

    @app.get("/api/review/candidates", dependencies=[Depends(admin_guard)])
    def candidates(
        confidence: str | None = Query(default=None),
        access: str | None = Query(default=None),
        uncertainty_band: Literal["under-100", "100-500", "over-500", "unknown"] | None = Query(default=None),
        source_type: str | None = Query(default=None, max_length=100),
    ) -> list[dict[str, object]]:
        if confidence is not None and confidence not in CONFIDENCE_LEVELS:
            raise HTTPException(400, "invalid candidate confidence")
        if access is not None and access not in SITE_ACCESSES:
            raise HTTPException(400, "invalid candidate access")
        conditions = ["sites.status = 'candidate'", "sites.merged_into_id IS NULL"]
        values: list[object] = []
        if confidence:
            conditions.append("COALESCE(sites.confidence, 'unknown') = ?")
            values.append(confidence)
        if access:
            conditions.append("sites.access = ?")
            values.append(access)
        if uncertainty_band == "under-100":
            conditions.append("sites.uncertainty_m IS NOT NULL AND sites.uncertainty_m <= 100")
        elif uncertainty_band == "100-500":
            conditions.append("sites.uncertainty_m > 100 AND sites.uncertainty_m <= 500")
        elif uncertainty_band == "over-500":
            conditions.append("sites.uncertainty_m > 500")
        elif uncertainty_band == "unknown":
            conditions.append("sites.uncertainty_m IS NULL")
        if source_type and source_type.strip():
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM evidence "
                "JOIN sources ON sources.id = evidence.source_id "
                "WHERE evidence.site_id = sites.id "
                "AND lower(sources.source_type) = lower(?)"
                ")"
            )
            values.append(source_type.strip())
        with database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY id",
                values,
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
        if "warnings" in values:
            values["warnings_json"] = dump_json(values.pop("warnings"))
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if "status" in values and values["status"] != row["status"]:
                raise HTTPException(409, "status changes must use the review workflow")
            values.pop("status", None)
            if not values:
                raise HTTPException(400, "no site fields supplied")
            assignments = ", ".join(f"{field} = ?" for field in values)
            values["updated_at"] = now_iso()
            params = [*values.values(), site_id]
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

    @app.post(
        "/api/sites/{site_id}/observations",
        status_code=201,
        dependencies=[Depends(admin_guard)],
    )
    def add_field_observation(site_id: int, observation: FieldObservation) -> dict[str, object]:
        timestamp = now_iso()
        payload = observation.model_dump(mode="json")
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            cursor = connection.execute(
                """
                INSERT INTO field_observations
                    (site_id, observed_at, outcome, note, latitude, longitude,
                     observed_location_text, access_notes, photo_urls_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site_id,
                    observation.observed_at.isoformat(),
                    observation.outcome,
                    observation.note,
                    observation.latitude,
                    observation.longitude,
                    observation.observed_location_text,
                    observation.access_notes,
                    dump_json(payload["photo_urls"]),
                    timestamp,
                ),
            )
            observation_row = connection.execute(
                "SELECT * FROM field_observations WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'observation', ?, ?)",
                (site_id, dump_json(payload), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {
                "observation": observation_from_row(observation_row),
                "site": _site_detail(connection, updated),
            }

    @app.post(
        "/api/sites/{site_id}/observations/{observation_id}/adopt-location",
        dependencies=[Depends(admin_guard)],
    )
    def adopt_observation_location(site_id: int, observation_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            observation = connection.execute(
                "SELECT * FROM field_observations WHERE id = ? AND site_id = ?",
                (observation_id, site_id),
            ).fetchone()
            if observation is None:
                raise HTTPException(404, "observation not found")
            if observation["outcome"] != "found":
                raise HTTPException(409, "only a found observation can update the site coordinate")
            if observation["latitude"] is None or observation["longitude"] is None:
                raise HTTPException(409, "observation has no coordinate")
            timestamp = now_iso()
            rationale = (row["short_rationale"] or "").strip()
            update_note = (
                f"Field observation #{observation_id} recorded {observation['observed_at']} "
                "was used to update the map coordinate."
            )
            rationale = f"{rationale} {update_note}".strip()[:2000]
            connection.execute(
                """
                UPDATE sites
                SET latitude = ?, longitude = ?, location_basis = ?, short_rationale = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    observation["latitude"],
                    observation["longitude"],
                    "explicit_coordinate",
                    rationale,
                    timestamp,
                    site_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO site_events (site_id, event_type, payload_json, created_at)
                VALUES (?, 'coordinate_adopted', ?, ?)
                """,
                (
                    site_id,
                    dump_json(
                        {
                            "observation_id": observation_id,
                            "old_latitude": row["latitude"],
                            "old_longitude": row["longitude"],
                            "new_latitude": observation["latitude"],
                            "new_longitude": observation["longitude"],
                        }
                    ),
                    timestamp,
                ),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": "coordinate_adopted", "site": _site_detail(connection, updated)}

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
                    "research": "likely",
                    "field_verify": "field-verified",
                    "confirm": "trusted",
                    "reject": "rejected",
                    "restore": "candidate",
                    "mark_approximate": "approximate",
                    "mark_destroyed": "destroyed-or-filled",
                }[request.action]
                allowed_statuses = {
                    "accept": {"candidate", "approximate"},
                    "research": {"candidate", "approximate"},
                    "field_verify": {"likely"},
                    "confirm": {"field-verified", "trusted"},
                    "restore": {"rejected"},
                    "mark_approximate": {"candidate"},
                    "mark_destroyed": {"candidate", "approximate", "likely", "field-verified", "trusted"},
                }
                if request.action in allowed_statuses and row["status"] not in allowed_statuses[request.action]:
                    raise HTTPException(409, "invalid lifecycle transition")
                if request.action == "field_verify":
                    if row["status"] != "likely":
                        raise HTTPException(409, "field verification requires a researched site")
                    observation = connection.execute(
                        "SELECT 1 FROM field_observations WHERE site_id = ? LIMIT 1",
                        (site_id,),
                    ).fetchone()
                    if observation is None:
                        raise HTTPException(409, "record a field observation before field verification")
                if request.action == "confirm" and row["status"] not in {
                    "field-verified",
                    "trusted",
                }:
                    raise HTTPException(409, "field verification is required before confirmation")
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
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
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
            "created_at": timestamp,
        }

    @app.get("/api/routes", dependencies=[Depends(admin_guard)])
    def list_routes(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, start_json, waypoints_json, distance_m, duration_s, created_at "
                "FROM route_plans ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_route_summary(row) for row in rows]

    @app.get("/api/routes/{route_id}", dependencies=[Depends(admin_guard)])
    def get_route(route_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM route_plans WHERE id = ?", (route_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "route not found")
            route = _route_summary(row)
            route["geometry"] = {"type": "LineString", "coordinates": json.loads(row["geometry_json"])}
            route["gpx"] = row["gpx_text"]
            route["warnings"] = ["A route does not grant permission to enter land or structures."]
            return route

    return app


app = create_app()
