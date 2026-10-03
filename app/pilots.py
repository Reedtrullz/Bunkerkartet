"""Disabled-by-default helpers for bounded product pilots.

The parent application owns migration allocation and route integration.  This
module exports the SQL contract but never applies it automatically.  Nothing
here turns a read credential into a write credential, silently syncs an
offline item, changes a site's trust/access, or publishes/hosts a package.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
from typing import Callable, Mapping, Sequence
from urllib.parse import quote
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field


PILOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_offline_packs (
    pack_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    site_revisions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pilot_offline_outbox (
    id INTEGER PRIMARY KEY,
    pack_id TEXT NOT NULL REFERENCES pilot_offline_packs(pack_id) ON DELETE CASCADE,
    original_request_id TEXT NOT NULL UNIQUE,
    site_key TEXT NOT NULL,
    cached_site_revision INTEGER NOT NULL CHECK(cached_site_revision >= 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending_review','needs_review','approved_for_sync','deferred','stale_pack')),
    review_reason TEXT,
    reviewer_ref TEXT,
    reviewed_at TEXT,
    reviewed_site_revision INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pilot_offline_outbox_pack ON pilot_offline_outbox(pack_id, id);

CREATE TABLE IF NOT EXISTS pilot_historical_assertions (
    id INTEGER PRIMARY KEY,
    assertion_key TEXT NOT NULL UNIQUE,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_key TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_label TEXT,
    certainty TEXT NOT NULL CHECK(certainty IN ('certain','probable','possible','uncertain','contradicted')),
    date_context TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    date_precision TEXT NOT NULL CHECK(date_precision IN ('day','month','year','range','unknown')),
    uncertainty TEXT NOT NULL,
    curator_disposition TEXT NOT NULL CHECK(curator_disposition IN ('pending','accepted','rejected','deferred')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pilot_historical_time ON pilot_historical_assertions(valid_from, valid_to, assertion_key);

CREATE TABLE IF NOT EXISTS pilot_reader_credentials (
    id INTEGER PRIMARY KEY,
    credential_id TEXT NOT NULL UNIQUE,
    salt_hex TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pilot_reader_expiry ON pilot_reader_credentials(expires_at, revoked_at);

CREATE TABLE IF NOT EXISTS pilot_publication_receipts (
    id INTEGER PRIMARY KEY,
    package_id TEXT NOT NULL UNIQUE,
    preview_sha256 TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    package_json TEXT NOT NULL,
    approval_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

PILOT_REQUIRED: dict[str, set[str]] = {
    "pilot_offline_packs": {
        "pack_id", "payload_json", "payload_sha256", "site_revisions_json", "created_at", "expires_at",
    },
    "pilot_offline_outbox": {
        "id", "pack_id", "original_request_id", "site_key", "cached_site_revision", "payload_json",
        "payload_sha256", "state", "review_reason", "reviewer_ref", "reviewed_at",
        "reviewed_site_revision", "created_at",
    },
    "pilot_historical_assertions": {
        "id", "assertion_key", "subject_key", "predicate", "object_key", "source_ref", "source_label",
        "certainty", "date_context", "valid_from", "valid_to", "date_precision", "uncertainty",
        "curator_disposition", "created_at",
    },
    "pilot_reader_credentials": {
        "id", "credential_id", "salt_hex", "token_hash", "scopes_json", "created_at", "expires_at", "revoked_at",
    },
    "pilot_publication_receipts": {
        "id", "package_id", "preview_sha256", "package_sha256", "package_json", "approval_json", "created_at",
    },
}


class PilotDisabled(RuntimeError):
    """A feature was called without its explicit owner-approved policy flag."""


class PilotError(ValueError):
    """A bounded pilot request is invalid."""


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PilotError("pilot data must be finite JSON values") from error


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise PilotError("timestamp must be ISO 8601 with a timezone") from error
    else:
        raise PilotError("timestamp must be an aware datetime or ISO 8601 string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PilotError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _iso(value: datetime | str | None) -> str:
    return _timestamp(value).isoformat().replace("+00:00", "Z")


def _require_enabled(enabled: bool, name: str) -> None:
    if not enabled:
        raise PilotDisabled(f"{name} pilot is disabled")


@dataclass(frozen=True)
class OfflinePolicy:
    enabled: bool = False
    max_sites: int = 50
    max_stored_packs: int = 100
    max_pack_bytes: int = 256_000
    max_ttl_seconds: int = 7 * 24 * 60 * 60
    max_outbox_items: int = 100
    max_item_bytes: int = 16_000
    allowed_fields: frozenset[str] = frozenset({
        "external_key", "name", "site_kind", "latitude", "longitude", "precision",
        "uncertainty_m", "revision",
    })

    def __post_init__(self) -> None:
        if not 1 <= self.max_sites <= 500 or not 1 <= self.max_stored_packs <= 1_000:
            raise ValueError("offline stored pack limit is outside the hard bound")
        if not 256 <= self.max_pack_bytes <= 2_000_000:
            raise ValueError("offline pack limits are outside the hard bounds")
        if not 60 <= self.max_ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("offline TTL must be between one minute and 30 days")
        if not 1 <= self.max_outbox_items <= 500 or not 256 <= self.max_item_bytes <= 64_000:
            raise ValueError("offline outbox limits are outside the hard bounds")


@dataclass(frozen=True)
class HistoricalPolicy:
    enabled: bool = False
    max_assertions: int = 5_000
    max_source_ref_length: int = 2_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_assertions <= 20_000 or not 16 <= self.max_source_ref_length <= 8_192:
            raise ValueError("historical pilot limits are outside the hard bounds")


READ_SCOPES = frozenset({"sites:read", "history:read", "publication-preview:read"})


@dataclass(frozen=True)
class ReaderPolicy:
    enabled: bool = False
    max_ttl_seconds: int = 24 * 60 * 60
    max_active_credentials: int = 100
    max_stored_credentials: int = 500
    allowed_scopes: frozenset[str] = READ_SCOPES

    def __post_init__(self) -> None:
        if not 60 <= self.max_ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("reader TTL must be between one minute and 30 days")
        if not 1 <= self.max_active_credentials <= 1_000:
            raise ValueError("reader credential limit is outside the hard bounds")
        if not 1 <= self.max_stored_credentials <= 5_000:
            raise ValueError("stored reader credential limit is outside the hard bounds")
        if not self.allowed_scopes <= READ_SCOPES:
            raise ValueError("reader scopes may only grant read access")


PUBLICATION_FIELDS = frozenset({
    "external_key", "name", "site_kind", "latitude", "longitude", "precision", "uncertainty_m",
})


@dataclass(frozen=True)
class PublicationPolicy:
    enabled: bool = False
    max_records: int = 200
    max_package_bytes: int = 1_000_000
    max_receipts: int = 500
    allowed_fields: frozenset[str] = PUBLICATION_FIELDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_records <= 1_000 or not 1_024 <= self.max_package_bytes <= 5_000_000:
            raise ValueError("publication package limits are outside the hard bounds")
        if not 1 <= self.max_receipts <= 5_000:
            raise ValueError("publication receipt limit is outside the hard bounds")
        if not self.allowed_fields <= PUBLICATION_FIELDS:
            raise ValueError("publication fields include a non-publication field")


@dataclass(frozen=True)
class GeographyDescriptor:
    descriptor_id: str
    display_name: str
    key_namespace: str
    bounding_box: tuple[float, float, float, float]  # min longitude, min latitude, max longitude, max latitude
    map_defaults: Mapping[str, object]
    approved_source_namespaces: tuple[str, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.display_name,str) or not self.display_name.strip() or len(self.display_name)>200:
            raise ValueError("geography display name must be bounded")
        if not 1 <= len(self.approved_source_namespaces) <= 20 or len(set(self.approved_source_namespaces)) != len(self.approved_source_namespaces) or any(not isinstance(item,str) or not item.strip() or len(item)>120 for item in self.approved_source_namespaces):
            raise ValueError("geography source namespaces must be unique and bounded")
        if not 1 <= len(self.warnings) <= 10 or any(not isinstance(item,str) or not item.strip() or len(item)>1000 for item in self.warnings):
            raise ValueError("geography scope warnings must be bounded")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", self.descriptor_id):
            raise ValueError("geography descriptor id is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", self.key_namespace):
            raise ValueError("geography namespace is invalid")
        if len(self.bounding_box) != 4:
            raise ValueError("geography bounding box must have four values")
        min_lon, min_lat, max_lon, max_lat = self.bounding_box
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError("geography bounding box is invalid")
        center = self.map_defaults.get("center")
        zoom = self.map_defaults.get("zoom")
        if (not isinstance(center, (tuple, list)) or len(center) != 2
                or not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in center)
                or not isinstance(zoom, int) or isinstance(zoom, bool) or not 1 <= zoom <= 18):
            raise ValueError("geography map defaults are invalid")
        if not min_lat <= center[0] <= max_lat or not min_lon <= center[1] <= max_lon:
            raise ValueError("geography map and start defaults must be within the selected scope")

    def as_dict(self) -> dict[str, object]:
        return {
            "descriptor_id": self.descriptor_id,
            "display_name": self.display_name,
            "key_namespace": self.key_namespace,
            "bounding_box": list(self.bounding_box),
            "map_defaults": dict(self.map_defaults),
            "approved_source_namespaces": list(self.approved_source_namespaces),
            "warnings": list(self.warnings),
            "synthetic": self.descriptor_id.startswith("synthetic-"),
        }


SYNTHETIC_GEOGRAPHIES = (
    GeographyDescriptor(
        "synthetic-alpha", "Synthetic scope Alpha", "bk-synthetic-alpha",
        (-30.0, -10.0, -20.0, 0.0),
        {"center": [-5.0, -25.0], "zoom": 7, "basemap": "configured-provider-policy"},
        ("source-alpha", "source-alpha:sub"),
        ("Synthetic coordinates and bounds; not a real jurisdiction or owner-selected operating area.",
         "Out-of-scope records remain unchanged and require curator review."),
    ),
    GeographyDescriptor(
        "synthetic-beta", "Synthetic scope Beta", "bk-synthetic-beta",
        (40.0, 20.0, 50.0, 30.0),
        {"center": [25.0, 45.0], "zoom": 6, "basemap": "configured-provider-policy"},
        ("source-beta",),
        ("Synthetic coordinates and bounds; not a real jurisdiction or owner-selected operating area.",
         "Geography is selected explicitly and is never inferred from GPS."),
    ),
)


@dataclass(frozen=True)
class GeographyPolicy:
    enabled: bool = False
    descriptors: tuple[GeographyDescriptor, ...] = ()

    def __post_init__(self) -> None:
        ids = [item.descriptor_id for item in self.descriptors]
        namespaces = [item.key_namespace for item in self.descriptors]
        if len(ids) != len(set(ids)) or len(namespaces) != len(set(namespaces)):
            raise ValueError("geography descriptor IDs and namespaces must be unique")
        if len(self.descriptors) > 10:
            raise ValueError("geography descriptor count exceeds the hard bound")


@dataclass(frozen=True)
class PilotPolicies:
    offline: OfflinePolicy = field(default_factory=OfflinePolicy)
    historical: HistoricalPolicy = field(default_factory=HistoricalPolicy)
    readers: ReaderPolicy = field(default_factory=ReaderPolicy)
    publication: PublicationPolicy = field(default_factory=PublicationPolicy)
    geography: GeographyPolicy = field(default_factory=GeographyPolicy)


def _policies_from_settings(settings: object | None) -> PilotPolicies:
    """Read opt-in booleans only; absent settings always means disabled."""
    if settings is None:
        return PilotPolicies()
    return PilotPolicies(
        offline=OfflinePolicy(enabled=bool(getattr(settings, "pilot_offline_enabled", False))),
        historical=HistoricalPolicy(enabled=bool(getattr(settings, "pilot_historical_enabled", False))),
        readers=ReaderPolicy(enabled=bool(getattr(settings, "pilot_readers_enabled", False))),
        publication=PublicationPolicy(enabled=bool(getattr(settings, "pilot_publication_enabled", False))),
        geography=GeographyPolicy(
            enabled=bool(getattr(settings, "pilot_geography_enabled", False)),
            descriptors=tuple(getattr(settings, "pilot_geography_descriptors", ())),
        ),
    )


def _validate_fields(fields: Sequence[str], allowed: frozenset[str]) -> tuple[str, ...]:
    if not fields or len(fields) > len(allowed) or len(set(fields)) != len(fields):
        raise PilotError("selected fields must be a non-empty unique bounded list")
    if any(not isinstance(item, str) or item not in allowed for item in fields):
        raise PilotError("selected fields include a field outside the explicit allowlist")
    return tuple(fields)


def _site_revisions(payload: Mapping[str, object]) -> dict[str, int]:
    records = payload.get("records")
    if not isinstance(records, list):
        raise PilotError("offline pack records are invalid")
    result: dict[str, int] = {}
    for item in records:
        if not isinstance(item, dict):
            raise PilotError("offline pack record is invalid")
        key = item.get("external_key")
        revision = item.get("revision")
        if not isinstance(key, str) or not key or len(key) > 300:
            raise PilotError("offline records require a bounded external key")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise PilotError("offline records require a non-negative server revision")
        if key in result:
            raise PilotError("offline pack contains duplicate site keys")
        result[key] = revision
    return result


def build_offline_pack(
    connection: sqlite3.Connection,
    selected_sites: Sequence[Mapping[str, object]],
    selected_fields: Sequence[str],
    policy: OfflinePolicy,
    *,
    now: datetime | str | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "offline notebook")
    fields = _validate_fields(selected_fields, policy.allowed_fields)
    if not selected_sites or len(selected_sites) > policy.max_sites:
        raise PilotError("offline site limit is empty or exceeded")
    ttl = policy.max_ttl_seconds if ttl_seconds is None else ttl_seconds
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= policy.max_ttl_seconds:
        raise PilotError("offline pack TTL exceeds the explicit policy")
    created = _timestamp(now)
    expires = created + timedelta(seconds=ttl)
    stored_pack_count = connection.execute("SELECT count(*) FROM pilot_offline_packs").fetchone()[0]
    if stored_pack_count >= policy.max_stored_packs:
        raise PilotError("stored offline pack limit reached; explicitly clear retired packs")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for site in selected_sites:
        key = site.get("external_key")
        if not isinstance(key, str) or not key or len(key) > 300 or key in seen:
            raise PilotError("offline pack external keys must be unique and bounded")
        seen.add(key)
        _site_revisions({"records": [dict(site)]})
        records.append({field_name: site[field_name] for field_name in fields if field_name in site})
        if "external_key" not in records[-1] or "revision" not in records[-1]:
            raise PilotError("offline packs must select external_key and revision")
    pack_id = uuid.uuid4().hex
    payload = {
        "schema": "bunkerkartet-offline-pack-v1",
        "pack_id": pack_id,
        "created_at": _iso(created),
        "expires_at": _iso(expires),
        "selected_fields": list(fields),
        "records": records,
    }
    payload_json = _canonical(payload)
    if len(payload_json.encode("utf-8")) > policy.max_pack_bytes:
        raise PilotError("offline pack exceeds the byte limit")
    revisions = _site_revisions(payload)
    digest = _sha256(payload_json)
    connection.execute(
        """INSERT INTO pilot_offline_packs
           (pack_id,payload_json,payload_sha256,site_revisions_json,created_at,expires_at)
           VALUES(?,?,?,?,?,?)""",
        (pack_id, payload_json, digest, _canonical(revisions), _iso(created), _iso(expires)),
    )
    return {
        "pack_id": pack_id,
        "payload_json": payload_json,
        "sha256": digest,
        "created_at": _iso(created),
        "expires_at": _iso(expires),
        "site_count": len(records),
        "stale": False,
        "tile_cache_included": False,
        "bearer_token_included": False,
    }


def inspect_offline_pack(
    connection: sqlite3.Connection,
    pack_id: str,
    policy: OfflinePolicy,
    *,
    now: datetime | str | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "offline notebook")
    row = connection.execute("SELECT * FROM pilot_offline_packs WHERE pack_id=?", (pack_id,)).fetchone()
    if row is None:
        raise KeyError("offline pack not found")
    payload_json = str(row["payload_json"])
    digest = _sha256(payload_json)
    if not hmac.compare_digest(digest, str(row["payload_sha256"])):
        raise PilotError("offline pack checksum mismatch")
    payload = json.loads(payload_json)
    if _canonical(payload) != payload_json or _site_revisions(payload) != json.loads(row["site_revisions_json"]):
        raise PilotError("offline pack content is not canonical or revision index changed")
    stale = _timestamp(now) >= _timestamp(row["expires_at"])
    return {
        "pack_id": pack_id,
        "payload": payload,
        "sha256": digest,
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "stale": stale,
        "label": "stale" if stale else "current",
    }


def clear_offline_pack(
    connection: sqlite3.Connection,
    pack_id: str,
    policy: OfflinePolicy,
    *,
    explicit: bool,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "offline notebook")
    if explicit is not True:
        raise PilotError("clearing an approved local pack requires an explicit clear command")
    cursor = connection.execute("DELETE FROM pilot_offline_packs WHERE pack_id=?", (pack_id,))
    if cursor.rowcount == 0:
        raise KeyError("offline pack not found")
    return {"pack_id": pack_id, "cleared": True, "outbox_cleared": True}


_FORBIDDEN_OFFLINE_KEYS = frozenset({
    "authorization", "bearer_token", "token", "photo", "photos", "photo_urls", "image", "attachment",
    "raw_import_payload", "route_start", "route_starts", "tile_cache", "tiles",
})


def _check_offline_payload(value: object) -> None:
    if isinstance(value, dict):
        if any(str(key).casefold() in _FORBIDDEN_OFFLINE_KEYS for key in value):
            raise PilotError("offline observation contains a forbidden credential, photo, route, or raw payload field")
        for nested in value.values():
            _check_offline_payload(nested)
    elif isinstance(value, list):
        for nested in value:
            _check_offline_payload(nested)


def queue_offline_observation(
    connection: sqlite3.Connection,
    pack_id: str,
    original_request_id: str,
    site_key: str,
    cached_site_revision: int,
    payload: Mapping[str, object],
    policy: OfflinePolicy,
    *,
    now: datetime | str | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "offline notebook")
    pack = inspect_offline_pack(connection, pack_id, policy, now=now)
    if pack["stale"]:
        raise PilotError("stale offline pack cannot accept outbox items")
    if not isinstance(original_request_id, str) or not 1 <= len(original_request_id) <= 128:
        raise PilotError("original request ID must be 1..128 characters")
    if not isinstance(site_key, str) or not 1 <= len(site_key) <= 300:
        raise PilotError("offline site key must be bounded")
    revisions = _site_revisions(pack["payload"])
    if site_key not in revisions or isinstance(cached_site_revision, bool) or revisions[site_key] != cached_site_revision:
        raise PilotError("outbox site and revision must match the selected offline pack")
    _check_offline_payload(dict(payload))
    payload_json = _canonical(dict(payload))
    if len(payload_json.encode("utf-8")) > policy.max_item_bytes:
        raise PilotError("offline outbox item exceeds the byte limit")
    timestamp = _iso(now)
    digest = _sha256(payload_json)
    existing = connection.execute(
        "SELECT id,pack_id,site_key,cached_site_revision,payload_sha256,state "
        "FROM pilot_offline_outbox WHERE original_request_id=?",
        (original_request_id,),
    ).fetchone()
    if existing is not None:
        if (existing["pack_id"] != pack_id or existing["site_key"] != site_key
                or existing["cached_site_revision"] != cached_site_revision
                or not hmac.compare_digest(str(existing["payload_sha256"]), digest)):
            raise PilotError("original request ID was already used with different content")
        return {"outbox_id": int(existing["id"]), "original_request_id": original_request_id,
                "duplicate_retry": True, "state": str(existing["state"])}
    count = connection.execute("SELECT count(*) FROM pilot_offline_outbox WHERE pack_id=?", (pack_id,)).fetchone()[0]
    if count >= policy.max_outbox_items:
        raise PilotError("offline outbox item limit exceeded")
    cursor = connection.execute(
        """INSERT INTO pilot_offline_outbox
           (pack_id,original_request_id,site_key,cached_site_revision,payload_json,payload_sha256,state,created_at)
           VALUES(?,?,?,?,?,?,'pending_review',?)""",
        (pack_id, original_request_id, site_key, cached_site_revision, payload_json, digest, timestamp),
    )
    return {"outbox_id": int(cursor.lastrowid), "original_request_id": original_request_id,
            "duplicate_retry": False, "state": "pending_review"}


def assess_offline_item(
    connection: sqlite3.Connection,
    pack_id: str,
    original_request_id: str,
    *,
    current_site_revision: int | None,
    policy: OfflinePolicy,
    now: datetime | str | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "offline notebook")
    pack = inspect_offline_pack(connection, pack_id, policy, now=now)
    row = connection.execute(
        "SELECT * FROM pilot_offline_outbox WHERE pack_id=? AND original_request_id=?",
        (pack_id, original_request_id),
    ).fetchone()
    if row is None:
        raise KeyError("offline outbox item not found")
    if pack["stale"]:
        state, reason = "stale_pack", "pack_expired"
    elif current_site_revision is None or current_site_revision != row["cached_site_revision"]:
        state, reason = "needs_review", "site_changed"
    elif row["state"] in {"approved_for_sync", "deferred"}:
        state, reason = str(row["state"]), row["review_reason"]
    else:
        state, reason = "pending_review", None
    if state != row["state"]:
        connection.execute(
            """UPDATE pilot_offline_outbox SET state=?,review_reason=?,reviewer_ref=NULL,
               reviewed_at=NULL,reviewed_site_revision=NULL WHERE id=?""",
            (state, reason, row["id"]),
        )
    else:
        connection.execute("UPDATE pilot_offline_outbox SET review_reason=? WHERE id=?", (reason, row["id"]))
    return {"pack_id": pack_id, "original_request_id": original_request_id,
            "state": state, "reason": reason, "manual_review_required": state in {"pending_review", "needs_review"},
            "synced": False}


def record_offline_review(
    connection: sqlite3.Connection,
    pack_id: str,
    original_request_id: str,
    *,
    decision: str,
    reviewer_ref: str,
    current_site_revision: int | None,
    policy: OfflinePolicy,
    now: datetime | str | None = None,
) -> dict[str, object]:
    assessment = assess_offline_item(connection, pack_id, original_request_id,
                                     current_site_revision=current_site_revision, policy=policy, now=now)
    if assessment["state"] == "stale_pack":
        raise PilotError("expired pack must be cleared and rebuilt before review")
    row = connection.execute(
        "SELECT * FROM pilot_offline_outbox WHERE pack_id=? AND original_request_id=?",
        (pack_id, original_request_id),
    ).fetchone()
    if decision not in {"approve", "defer"}:
        raise PilotError("manual offline review decision must be approve or defer")
    if not isinstance(reviewer_ref, str) or not 1 <= len(reviewer_ref.strip()) <= 200:
        raise PilotError("manual review requires a bounded reviewer reference")
    if decision == "approve" and (
        current_site_revision is None or current_site_revision != row["cached_site_revision"]
    ):
        raise PilotError("changed site must be reviewed against a current pack before approval")
    state = "approved_for_sync" if decision == "approve" else "deferred"
    connection.execute(
        """UPDATE pilot_offline_outbox SET state=?,review_reason=?,reviewer_ref=?,reviewed_at=?,
           reviewed_site_revision=? WHERE id=?""",
        (state, "explicit_manual_review", reviewer_ref.strip(), _iso(now), current_site_revision, row["id"]),
    )
    return {"pack_id": pack_id, "original_request_id": original_request_id, "state": state,
            "reviewer_ref": reviewer_ref.strip(), "reviewed_site_revision": current_site_revision,
            "synced": False}


@dataclass(frozen=True)
class HistoricalAssertion:
    assertion_key: str
    subject_key: str
    predicate: str
    object_key: str
    source_ref: str
    certainty: str
    date_context: str
    valid_from: str | None
    valid_to: str | None
    date_precision: str
    curator_disposition: str
    uncertainty: str
    source_label: str | None = None


def _validate_historical(assertion: HistoricalAssertion, policy: HistoricalPolicy) -> None:
    for name, value, limit in (
        ("assertion key", assertion.assertion_key, 300), ("subject key", assertion.subject_key, 300),
        ("predicate", assertion.predicate, 100), ("object key", assertion.object_key, 300),
        ("source reference", assertion.source_ref, policy.max_source_ref_length),
        ("date context", assertion.date_context, 1_000), ("uncertainty", assertion.uncertainty, 2_000),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise PilotError(f"historical {name} is required and bounded")
    if assertion.source_label is not None and (not assertion.source_label.strip() or len(assertion.source_label) > 500):
        raise PilotError("historical source label must be bounded when supplied")
    if assertion.certainty not in {"certain", "probable", "possible", "uncertain", "contradicted"}:
        raise PilotError("historical certainty value is invalid")
    if assertion.curator_disposition not in {"pending", "accepted", "rejected", "deferred"}:
        raise PilotError("historical curator disposition is invalid")
    if assertion.date_precision not in {"day", "month", "year", "range", "unknown"}:
        raise PilotError("historical date precision is invalid")
    if assertion.date_precision == "unknown":
        if assertion.valid_from is not None or assertion.valid_to is not None:
            raise PilotError("unknown historical dates cannot carry interval endpoints")
        return
    if assertion.valid_from is None and assertion.valid_to is None:
        raise PilotError("historical interval precision requires at least one date endpoint")
    try:
        start = date.fromisoformat(assertion.valid_from) if assertion.valid_from is not None else None
        end = date.fromisoformat(assertion.valid_to) if assertion.valid_to is not None else None
    except ValueError as error:
        raise PilotError("historical interval endpoints must be ISO dates") from error
    if start is not None and end is not None and start > end:
        raise PilotError("historical date interval start must not follow its end")


def add_historical_assertion(
    connection: sqlite3.Connection,
    assertion: HistoricalAssertion,
    policy: HistoricalPolicy,
    *,
    now: datetime | str | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "historical assertions")
    _validate_historical(assertion, policy)
    count = connection.execute("SELECT count(*) FROM pilot_historical_assertions").fetchone()[0]
    if count >= policy.max_assertions:
        raise PilotError("historical assertion limit exceeded")
    try:
        cursor = connection.execute(
            """INSERT INTO pilot_historical_assertions
               (assertion_key,subject_key,predicate,object_key,source_ref,source_label,certainty,date_context,
                valid_from,valid_to,date_precision,uncertainty,curator_disposition,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (assertion.assertion_key, assertion.subject_key, assertion.predicate, assertion.object_key,
             assertion.source_ref, assertion.source_label, assertion.certainty, assertion.date_context,
             assertion.valid_from, assertion.valid_to, assertion.date_precision, assertion.uncertainty,
             assertion.curator_disposition, _iso(now)),
        )
    except sqlite3.IntegrityError as error:
        raise PilotError("historical assertion key already exists") from error
    return {**asdict(assertion), "id": int(cursor.lastrowid), "created_at": _iso(now),
            "affects_site_trust": False, "affects_site_access": False}


def list_time_layer(
    connection: sqlite3.Connection,
    on_date: str | date | None,
    policy: HistoricalPolicy,
) -> list[dict[str, object]]:
    _require_enabled(policy.enabled, "historical assertions")
    day = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    rows = connection.execute(
        "SELECT * FROM pilot_historical_assertions ORDER BY COALESCE(valid_from,'9999-12-31'),assertion_key"
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        if day is None or item["date_precision"] == "unknown":
            match = "unknown" if item["date_precision"] == "unknown" else "not_filtered"
        elif item["valid_from"] is not None and day < date.fromisoformat(item["valid_from"]):
            continue
        elif item["valid_to"] is not None and day > date.fromisoformat(item["valid_to"]):
            continue
        else:
            match = "within_interval"
        item["date_match"] = match
        item["visually_distinct_uncertainty"] = item["certainty"] in {"possible", "uncertain", "contradicted"}
        item["affects_site_trust"] = False
        item["affects_site_access"] = False
        result.append(item)
    return result


@dataclass(frozen=True)
class ReaderGrant:
    credential_id: str
    scopes: frozenset[str]
    expires_at: str
    read_only: bool = True

    def require_scope(self, scope: str) -> None:
        if scope not in READ_SCOPES or scope not in self.scopes:
            raise PermissionError("reader credential lacks the requested read scope")

    def require_mutation(self, action: str = "mutation") -> None:
        del action
        raise PermissionError("reader credentials are read-only and cannot authorize mutations")


def issue_reader_credential(
    connection: sqlite3.Connection,
    policy: ReaderPolicy,
    *,
    now: datetime | str | None,
    ttl_seconds: int,
    scopes: Sequence[str],
) -> dict[str, object]:
    _require_enabled(policy.enabled, "private reader")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= policy.max_ttl_seconds:
        raise PilotError("reader TTL exceeds the explicit policy")
    if not scopes or len(scopes) > len(READ_SCOPES) or len(set(scopes)) != len(scopes):
        raise PilotError("reader scopes must be a non-empty unique list")
    if not set(scopes) <= policy.allowed_scopes or not set(scopes) <= READ_SCOPES:
        raise PilotError("reader credential is read-only; mutation scopes are forbidden")
    created = _timestamp(now)
    expires = created + timedelta(seconds=ttl_seconds)
    active_count = connection.execute(
        "SELECT count(*) FROM pilot_reader_credentials WHERE revoked_at IS NULL AND expires_at>?",
        (_iso(created),),
    ).fetchone()[0]
    if active_count >= policy.max_active_credentials:
        raise PilotError("active reader credential limit exceeded")
    stored_count = connection.execute("SELECT count(*) FROM pilot_reader_credentials").fetchone()[0]
    if stored_count >= policy.max_stored_credentials:
        raise PilotError("stored reader credential limit reached")
    token = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    token_hash = hashlib.sha256(salt + token.encode("utf-8")).hexdigest()
    credential_id = uuid.uuid4().hex
    connection.execute(
        """INSERT INTO pilot_reader_credentials
           (credential_id,salt_hex,token_hash,scopes_json,created_at,expires_at,revoked_at)
           VALUES(?,?,?,?,?,?,NULL)""",
        (credential_id, salt.hex(), token_hash, _canonical(sorted(scopes)), _iso(created), _iso(expires)),
    )
    return {"credential_id": credential_id, "token": token, "scopes": sorted(scopes),
            "created_at": _iso(created), "expires_at": _iso(expires), "returned_once": True,
            "read_only": True}


def authorize_reader(
    connection: sqlite3.Connection,
    token: str,
    now: datetime | str | None,
) -> ReaderGrant:
    """Resolve an unexpired bearer token to a read-only grant for a GET guard.

    The parent must check its reader policy is enabled before using this helper
    and must keep mutation routes behind its independent admin guard.
    """
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        raise PermissionError("reader credential is invalid")
    candidate = token[7:].strip() if token.startswith("Bearer ") else token
    if not candidate or not 20 <= len(candidate) <= 256:
        raise PermissionError("reader credential is invalid")
    current = _iso(now)
    rows = connection.execute(
        "SELECT credential_id,salt_hex,token_hash,scopes_json,expires_at FROM pilot_reader_credentials "
        "WHERE revoked_at IS NULL AND expires_at>? ORDER BY id",
        (current,),
    ).fetchall()
    for row in rows:
        try:
            expected = hashlib.sha256(bytes.fromhex(row["salt_hex"]) + candidate.encode("utf-8")).hexdigest()
            scopes = json.loads(row["scopes_json"])
            if not isinstance(scopes, list) or not set(scopes) <= READ_SCOPES:
                continue
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if hmac.compare_digest(expected, str(row["token_hash"])):
            return ReaderGrant(str(row["credential_id"]), frozenset(scopes), str(row["expires_at"]))
    raise PermissionError("reader credential is invalid, expired, or revoked")


def revoke_reader_credential(
    connection: sqlite3.Connection,
    credential_id: str,
    policy: ReaderPolicy,
    *,
    now: datetime | str | None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "private reader")
    cursor = connection.execute(
        "UPDATE pilot_reader_credentials SET revoked_at=? WHERE credential_id=? AND revoked_at IS NULL",
        (_iso(now), credential_id),
    )
    if cursor.rowcount == 0:
        raise KeyError("active reader credential not found")
    return {"credential_id": credential_id, "revoked": True, "revoked_at": _iso(now)}


@dataclass(frozen=True)
class PublicationDecision:
    selection: str
    selection_ref: str
    rights: str
    rights_ref: str
    coordinate_mode: str
    coordinate_ref: str
    generalized_latitude: float | None = None
    generalized_longitude: float | None = None


def _approval_ref(value: str, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
        raise PilotError(f"publication {label} reference is required")
    return value.strip()


def _publication_hash_content(preview: Mapping[str, object]) -> tuple[str, str]:
    membership = {
        "selected_fields": preview["selected_fields"],
        "entries": preview["entries"],
        "excluded": preview["excluded"],
    }
    preview_hash = _sha256(_canonical(membership))
    package_body = {
        "schema": preview["schema"],
        "package_id": preview["package_id"],
        "preview_sha256": preview_hash,
        "approvals": preview["approvals"],
    }
    package_hash = _sha256(_canonical(package_body))
    return preview_hash, package_hash


def build_publication_preview(
    records: Sequence[Mapping[str, object]],
    decisions: Mapping[str, PublicationDecision],
    selected_fields: Sequence[str],
    policy: PublicationPolicy,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "publication preview")
    fields = _validate_fields(selected_fields, policy.allowed_fields)
    if "external_key" not in fields:
        raise PilotError("publication field selection must include external_key")
    if not records or len(records) > policy.max_records:
        raise PilotError("publication record limit is empty or exceeded")
    keys = [record.get("external_key") for record in records]
    if any(not isinstance(key, str) or not key for key in keys) or len(keys) != len(set(keys)):
        raise PilotError("publication records require unique external keys")
    if set(decisions) != set(keys):
        raise PilotError("every preview candidate requires an explicit selection/rights/coordinate decision")
    entries: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    approvals: list[dict[str, object]] = []
    for record in records:
        key = str(record["external_key"])
        decision = decisions[key]
        if decision.selection not in {"include", "exclude"}:
            raise PilotError("publication selection decision must be include or exclude")
        selection_ref = _approval_ref(decision.selection_ref, "selection")
        rights_ref = _approval_ref(decision.rights_ref, "rights")
        coordinate_ref = _approval_ref(decision.coordinate_ref, "coordinate")
        if decision.rights not in {"approved", "pending", "denied"}:
            raise PilotError("publication rights decision is invalid")
        if decision.coordinate_mode not in {"exact", "generalized", "withheld"}:
            raise PilotError("publication coordinate decision is invalid")
        approval = {
            "external_key": key,
            "selection": decision.selection,
            "selection_ref": selection_ref,
            "rights": decision.rights,
            "rights_ref": rights_ref,
            "coordinate_mode": decision.coordinate_mode,
            "coordinate_ref": coordinate_ref,
        }
        if decision.selection == "exclude":
            excluded.append(approval)
            approvals.append(approval)
            continue
        if decision.rights != "approved":
            raise PilotError("publication inclusion requires an explicit approved rights decision")
        if decision.coordinate_mode == "withheld" and ({"latitude", "longitude"} & set(fields)):
            raise PilotError("withheld coordinates cannot be included in selected publication fields")
        item: dict[str, object] = {}
        for field_name in fields:
            if field_name in {"latitude", "longitude"}:
                continue
            if field_name not in record:
                raise PilotError(f"selected publication field {field_name} is absent from the record")
            item[field_name] = record[field_name]
        coordinate_fields = {"latitude", "longitude"} & set(fields)
        if coordinate_fields:
            if coordinate_fields != {"latitude", "longitude"}:
                raise PilotError("publication must select both coordinate axes or neither")
            if decision.coordinate_mode == "exact":
                latitude, longitude = record.get("latitude"), record.get("longitude")
            elif decision.coordinate_mode == "generalized":
                latitude, longitude = decision.generalized_latitude, decision.generalized_longitude
            else:
                raise PilotError("coordinate decision does not approve selected coordinates")
            if (isinstance(latitude, bool) or not isinstance(latitude, (int, float))
                    or not math.isfinite(float(latitude)) or not -90 <= latitude <= 90
                    or isinstance(longitude, bool) or not isinstance(longitude, (int, float))
                    or not math.isfinite(float(longitude)) or not -180 <= longitude <= 180):
                raise PilotError("publication coordinates require explicit finite WGS84 values")
            item["latitude"] = latitude
            item["longitude"] = longitude
        entries.append({"external_key": key, "fields": item, "approval_refs": approval})
        approvals.append(approval)
    preview: dict[str, object] = {
        "schema": "bunkerkartet-publication-preview-v1",
        "package_id": uuid.uuid4().hex,
        "selected_fields": list(fields),
        "entries": entries,
        "excluded": excluded,
        "approvals": approvals,
        "hosted": False,
        "deployed": False,
    }
    preview_hash, package_hash = _publication_hash_content(preview)
    preview["preview_sha256"] = preview_hash
    preview["package_sha256"] = package_hash
    encoded = _canonical(preview)
    if len(encoded.encode("utf-8")) > policy.max_package_bytes:
        raise PilotError("publication preview exceeds the byte limit")
    return preview


def verify_publication_preview(preview: Mapping[str, object]) -> bool:
    try:
        expected_preview, expected_package = _publication_hash_content(preview)
    except (KeyError, PilotError, TypeError):
        return False
    return (
        hmac.compare_digest(str(preview.get("preview_sha256", "")), expected_preview)
        and hmac.compare_digest(str(preview.get("package_sha256", "")), expected_package)
    )


def persist_publication_preview(
    connection: sqlite3.Connection,
    preview: Mapping[str, object],
    policy: PublicationPolicy,
    *,
    now: datetime | str | None = None,
) -> dict[str, object]:
    _require_enabled(policy.enabled, "publication preview")
    if not verify_publication_preview(preview):
        raise PilotError("publication preview hash verification failed")
    receipt_count = connection.execute("SELECT count(*) FROM pilot_publication_receipts").fetchone()[0]
    if receipt_count >= policy.max_receipts:
        raise PilotError("publication receipt limit exceeded")
    package_json = _canonical(dict(preview))
    approval_json = _canonical(preview["approvals"])
    connection.execute(
        """INSERT INTO pilot_publication_receipts
           (package_id,preview_sha256,package_sha256,package_json,approval_json,created_at)
           VALUES(?,?,?,?,?,?)""",
        (preview["package_id"], preview["preview_sha256"], preview["package_sha256"],
         package_json, approval_json, _iso(now)),
    )
    return {"package_id": preview["package_id"], "preview_sha256": preview["preview_sha256"],
            "package_sha256": preview["package_sha256"], "record_count": len(preview["entries"]),
            "hosted": False, "deployed": False}


def geography_key(descriptor: GeographyDescriptor, source_namespace: str, external_key: str) -> str:
    if not isinstance(source_namespace, str) or not 1 <= len(source_namespace) <= 120:
        raise PilotError("source namespace must be bounded")
    if source_namespace not in descriptor.approved_source_namespaces:
        raise PilotError("source namespace is not approved by this geography descriptor")
    if not isinstance(external_key, str) or not 1 <= len(external_key) <= 500:
        raise PilotError("external key must be bounded")
    return "geo:" + descriptor.key_namespace + ":" + quote(source_namespace, safe="") + ":" + quote(external_key, safe="")


def out_of_scope_review(
    descriptor: GeographyDescriptor,
    latitude: float,
    longitude: float,
) -> dict[str, object]:
    if (isinstance(latitude, bool) or not isinstance(latitude, (int, float)) or not math.isfinite(float(latitude))
            or not -90 <= latitude <= 90 or isinstance(longitude, bool)
            or not isinstance(longitude, (int, float)) or not math.isfinite(float(longitude))
            or not -180 <= longitude <= 180):
        raise PilotError("geographic scope check requires bounded WGS84 coordinates")
    min_lon, min_lat, max_lon, max_lat = descriptor.bounding_box
    within = min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat
    return {
        "descriptor_id": descriptor.descriptor_id,
        "review_required": not within,
        "status": "in_scope" if within else "outside_selected_scope_review_required",
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "coerced": False,
        "warnings": list(descriptor.warnings),
    }


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _OfflinePackBody(_Body):
    site_ids: list[int] = Field(min_length=1, max_length=50)
    selected_fields: list[str] = Field(min_length=2, max_length=20)
    ttl_seconds: int = Field(default=24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)


class _OfflineOutboxBody(_Body):
    original_request_id: str = Field(min_length=1, max_length=128)
    site_key: str = Field(min_length=1, max_length=300)
    cached_site_revision: int = Field(ge=0)
    payload: dict[str, object]


class _OfflineReviewBody(_Body):
    decision: str = Field(pattern="^(approve|defer)$")
    reviewer_ref: str = Field(min_length=1, max_length=200)


class _HistoricalBody(_Body):
    assertion_key: str = Field(min_length=1, max_length=300)
    subject_key: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=100)
    object_key: str = Field(min_length=1, max_length=300)
    source_ref: str = Field(min_length=1, max_length=2_000)
    certainty: str = Field(pattern="^(certain|probable|possible|uncertain|contradicted)$")
    date_context: str = Field(min_length=1, max_length=1_000)
    valid_from: str | None = None
    valid_to: str | None = None
    date_precision: str = Field(pattern="^(day|month|year|range|unknown)$")
    curator_disposition: str = Field(pattern="^(pending|accepted|rejected|deferred)$")
    uncertainty: str = Field(min_length=1, max_length=2_000)
    source_label: str | None = Field(default=None, max_length=500)


class _ReaderBody(_Body):
    scopes: list[str] = Field(min_length=1, max_length=3)
    ttl_seconds: int = Field(ge=60, le=30 * 24 * 60 * 60)


class _PublicationDecisionBody(_Body):
    selection: str = Field(pattern="^(include|exclude)$")
    selection_ref: str = Field(min_length=1, max_length=300)
    rights: str = Field(pattern="^(approved|pending|denied)$")
    rights_ref: str = Field(min_length=1, max_length=300)
    coordinate_mode: str = Field(pattern="^(exact|generalized|withheld)$")
    coordinate_ref: str = Field(min_length=1, max_length=300)
    generalized_latitude: float | None = Field(default=None, ge=-90, le=90)
    generalized_longitude: float | None = Field(default=None, ge=-180, le=180)


class _PublicationBody(_Body):
    site_keys: list[str] = Field(min_length=1, max_length=200)
    selected_fields: list[str] = Field(min_length=1, max_length=7)
    decisions: dict[str, _PublicationDecisionBody]


def _raise_http(error: Exception) -> None:
    if isinstance(error, PilotDisabled):
        raise HTTPException(404, "pilot disabled") from error
    if isinstance(error, PermissionError):
        raise HTTPException(403, "reader credential is not authorized") from error
    if isinstance(error, KeyError):
        raise HTTPException(404, str(error)) from error
    if isinstance(error, (PilotError, ValueError)):
        raise HTTPException(422, str(error)) from error
    raise error


def _site_representation(site_detail: Callable[..., Mapping[str, object]], connection: sqlite3.Connection,
                         row: sqlite3.Row) -> dict[str, object]:
    """Adapt the current parent helper shape: site_detail(connection, row)."""
    detail = dict(site_detail(connection, row))
    detail.setdefault("external_key", row["external_key"])
    detail.setdefault("revision", row["revision"])
    return detail


def install_pilot_routes(
    app: FastAPI,
    database: object,
    admin_guard: Callable[..., object],
    site_detail: Callable[..., Mapping[str, object]],
    settings: object | None = None,
    *,
    policies: PilotPolicies | None = None,
) -> None:
    """Register bounded pilot endpoints; the caller owns migration and policy.

    The injected ``site_detail`` callback follows the current app helper shape
    ``site_detail(connection, site_row)``.  Every write endpoint remains behind
    ``admin_guard``.  Parent GET guards may call :func:`authorize_reader` only
    after separately checking the reader feature flag.
    """
    if policies is not None and settings is not None:
        raise ValueError("pass either explicit policies or settings, not both")
    effective = policies if policies is not None else _policies_from_settings(settings)
    auth = [Depends(admin_guard)]

    def open_connection():
        connect = getattr(database, "connect", None)
        if not callable(connect):
            raise RuntimeError("pilot routes require a database with connect()")
        return connect()

    def enabled_names() -> list[str]:
        return [name for name, enabled in (
            ("offline", effective.offline.enabled), ("historical", effective.historical.enabled),
            ("readers", effective.readers.enabled), ("publication-preview", effective.publication.enabled),
            ("geography", effective.geography.enabled),
        ) if enabled]

    @app.get("/api/pilots/status", dependencies=auth)
    def pilot_status() -> dict[str, object]:
        return {"enabled": enabled_names(), "defaults_enabled": False, "production_data_used": False}

    @app.get("/api/pilots/geographies", dependencies=auth)
    def list_geographies() -> dict[str, object]:
        try:
            _require_enabled(effective.geography.enabled, "geography")
            return {"descriptors": [descriptor.as_dict() for descriptor in effective.geography.descriptors]}
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/offline/packs", dependencies=auth)
    def create_pack(body: _OfflinePackBody) -> dict[str, object]:
        try:
            _require_enabled(effective.offline.enabled, "offline notebook")
            if len(set(body.site_ids)) != len(body.site_ids):
                raise PilotError("offline site IDs must be unique")
            with open_connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM sites WHERE id IN (" + ",".join("?" for _ in body.site_ids) + ") ORDER BY id",
                    body.site_ids,
                ).fetchall()
                if len(rows) != len(set(body.site_ids)):
                    raise KeyError("one or more explicitly selected sites were not found")
                representations = [_site_representation(site_detail, connection, row) for row in rows]
                return build_offline_pack(connection, representations, body.selected_fields, effective.offline,
                                          ttl_seconds=body.ttl_seconds)
        except Exception as error:
            _raise_http(error)

    @app.get("/api/pilots/offline/packs/{pack_id}", dependencies=auth)
    def get_pack(pack_id: str) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return inspect_offline_pack(connection, pack_id, effective.offline)
        except Exception as error:
            _raise_http(error)

    @app.delete("/api/pilots/offline/packs/{pack_id}", dependencies=auth)
    def delete_pack(pack_id: str) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return clear_offline_pack(connection, pack_id, effective.offline, explicit=True)
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/offline/packs/{pack_id}/outbox", dependencies=auth)
    def enqueue_offline_item(pack_id: str, body: _OfflineOutboxBody) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return queue_offline_observation(
                    connection, pack_id, body.original_request_id, body.site_key,
                    body.cached_site_revision, body.payload, effective.offline,
                )
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/offline/packs/{pack_id}/outbox/{request_id}/review", dependencies=auth)
    def review_offline_item(pack_id: str, request_id: str, body: _OfflineReviewBody) -> dict[str, object]:
        try:
            with open_connection() as connection:
                item = connection.execute(
                    "SELECT site_key FROM pilot_offline_outbox WHERE pack_id=? AND original_request_id=?",
                    (pack_id, request_id),
                ).fetchone()
                if item is None:
                    raise KeyError("offline outbox item not found")
                current = connection.execute("SELECT revision FROM sites WHERE external_key=?", (item["site_key"],)).fetchone()
                revision = int(current["revision"]) if current is not None else None
                return record_offline_review(
                    connection, pack_id, request_id, decision=body.decision, reviewer_ref=body.reviewer_ref,
                    current_site_revision=revision, policy=effective.offline,
                )
        except Exception as error:
            _raise_http(error)

    @app.get("/api/pilots/historical/assertions", dependencies=auth)
    def get_historical_assertions(on_date: str | None = Query(default=None, max_length=10)) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return {"items": list_time_layer(connection, on_date, effective.historical)}
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/historical/assertions", dependencies=auth)
    def create_historical_assertion(body: _HistoricalBody) -> dict[str, object]:
        try:
            assertion = HistoricalAssertion(**body.model_dump())
            with open_connection() as connection:
                return add_historical_assertion(connection, assertion, effective.historical)
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/readers", dependencies=auth)
    def create_reader(body: _ReaderBody) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return issue_reader_credential(connection, effective.readers,
                                               now=None, ttl_seconds=body.ttl_seconds, scopes=body.scopes)
        except Exception as error:
            _raise_http(error)

    @app.delete("/api/pilots/readers/{credential_id}", dependencies=auth)
    def delete_reader(credential_id: str) -> dict[str, object]:
        try:
            with open_connection() as connection:
                return revoke_reader_credential(connection, credential_id, effective.readers, now=None)
        except Exception as error:
            _raise_http(error)

    @app.post("/api/pilots/publication/preview", dependencies=auth)
    def preview_publication(body: _PublicationBody) -> dict[str, object]:
        try:
            _require_enabled(effective.publication.enabled, "publication preview")
            if len(set(body.site_keys)) != len(body.site_keys):
                raise PilotError("publication site keys must be unique")
            with open_connection() as connection:
                records: list[dict[str, object]] = []
                for key in body.site_keys:
                    row = connection.execute("SELECT * FROM sites WHERE external_key=?", (key,)).fetchone()
                    if row is None:
                        raise KeyError("one or more publication candidates were not found")
                    records.append(_site_representation(site_detail, connection, row))
                decisions = {key: PublicationDecision(**value.model_dump()) for key, value in body.decisions.items()}
                preview = build_publication_preview(records, decisions, body.selected_fields, effective.publication)
                persist_publication_preview(connection, preview, effective.publication)
                return preview
        except Exception as error:
            _raise_http(error)
