"""Bounded, file-free evidence for a local affine historical-raster calibration.

This module does not load imagery, fetch URLs, consult current approaches, or
promote map annotations. A receipt is a reproducible calculation record; rights
and independent-review gates remain owner decisions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence


class RasterEvidenceError(ValueError):
    """The calibration inputs exceed or violate the documented pilot bounds."""


_RIGHTS_STATUSES = frozenset({"unknown", "restricted", "permission_recorded"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROJECTION = "local affine WGS84 lon/lat; spherical residual estimate"
_EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class CalibrationPolicy:
    """Owner-supplied comparison gate; the default is deliberately disabled."""

    enabled: bool = False
    policy_id: str | None = None
    approved_rights_references: tuple[str, ...] = ()
    independent_review_reference: str | None = None
    max_control_points: int = 32
    max_raster_pixels: int = 100_000_000
    max_residual_m: float | None = None
    min_scale_m_per_pixel: float | None = None
    max_scale_m_per_pixel: float | None = None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RasterEvidenceError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise RasterEvidenceError(f"{field} must be a finite number")
    return result


def _positive_optional(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    result = _number(value, field)
    if result <= 0:
        raise RasterEvidenceError(f"{field} must be positive")
    return result


def _validate_policy(policy: CalibrationPolicy) -> None:
    if not isinstance(policy.enabled, bool):
        raise RasterEvidenceError("policy enabled must be boolean")
    if not isinstance(policy.max_control_points, int) or not 3 <= policy.max_control_points <= 64:
        raise RasterEvidenceError("max_control_points must be between 3 and 64")
    if not isinstance(policy.max_raster_pixels, int) or not 4 <= policy.max_raster_pixels <= 500_000_000:
        raise RasterEvidenceError("max_raster_pixels is outside the hard safety bound")
    for field in ("policy_id", "independent_review_reference"):
        value = getattr(policy, field)
        if value is not None and (
            not isinstance(value, str) or not value.strip() or len(value.strip()) > 200
        ):
            raise RasterEvidenceError(f"{field} must be a short string")
    if not isinstance(policy.approved_rights_references, (tuple, list)) or any(
        not isinstance(ref, str) or not ref.strip() or len(ref) > 200
        for ref in policy.approved_rights_references
    ):
        raise RasterEvidenceError("approved rights references must be non-empty bounded strings")
    _positive_optional(policy.max_residual_m, "max_residual_m")
    minimum = _positive_optional(policy.min_scale_m_per_pixel, "min_scale_m_per_pixel")
    maximum = _positive_optional(policy.max_scale_m_per_pixel, "max_scale_m_per_pixel")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise RasterEvidenceError("minimum scale exceeds maximum scale")


def _haversine_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    lat1 = math.radians(lat_a)
    lat2 = math.radians(lat_b)
    dlat = lat2 - lat1
    dlon_deg = (lon_b - lon_a + 180.0) % 360.0 - 180.0
    dlon = math.radians(dlon_deg)
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(max(0.0, h))))


def _fit_axis(points: Sequence[Mapping[str, float]], field: str, center_x: float, center_y: float) -> tuple[float, float, float]:
    count = len(points)
    center_value = sum(point[field] for point in points) / count
    xx = sum((point["pixel_x"] - center_x) ** 2 for point in points)
    yy = sum((point["pixel_y"] - center_y) ** 2 for point in points)
    xy = sum((point["pixel_x"] - center_x) * (point["pixel_y"] - center_y) for point in points)
    xv = sum((point["pixel_x"] - center_x) * (point[field] - center_value) for point in points)
    yv = sum((point["pixel_y"] - center_y) * (point[field] - center_value) for point in points)
    determinant = xx * yy - xy * xy
    if determinant <= max(1.0, xx * yy) * 1e-12:
        raise RasterEvidenceError("control points are collinear or numerically singular")
    slope_x = (xv * yy - yv * xy) / determinant
    slope_y = (yv * xx - xv * xy) / determinant
    origin = center_value - slope_x * center_x - slope_y * center_y
    return origin, slope_x, slope_y


def _apply(matrix: Sequence[float], x: float, y: float) -> float:
    return matrix[0] + matrix[1] * x + matrix[2] * y


def _validate_raster(raster: Mapping[str, Any], policy: CalibrationPolicy) -> dict[str, Any]:
    raster_id = raster.get("raster_id")
    if not isinstance(raster_id, str) or not raster_id.strip() or len(raster_id) > 120:
        raise RasterEvidenceError("raster_id must be a non-empty bounded identifier")
    digest = raster.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
        raise RasterEvidenceError("raster sha256 must be a 64-character hexadecimal digest")
    width = raster.get("width_px")
    height = raster.get("height_px")
    if isinstance(width, bool) or not isinstance(width, int) or not 2 <= width <= 100_000:
        raise RasterEvidenceError("width_px must be between 2 and 100000")
    if isinstance(height, bool) or not isinstance(height, int) or not 2 <= height <= 100_000:
        raise RasterEvidenceError("height_px must be between 2 and 100000")
    if width * height > policy.max_raster_pixels:
        raise RasterEvidenceError("raster pixel count exceeds the policy bound")
    source_date = raster.get("source_date")
    if source_date is not None:
        if not isinstance(source_date, str):
            raise RasterEvidenceError("source_date must be an ISO date or null")
        try:
            date.fromisoformat(source_date)
        except ValueError as exc:
            raise RasterEvidenceError("source_date must be an ISO date or null") from exc
    sample_kind = raster.get("sample_kind", "synthetic")
    if not isinstance(sample_kind, str) or sample_kind not in {"synthetic", "owner_provided"}:
        raise RasterEvidenceError("sample_kind must be synthetic or owner_provided")
    return {
        "raster_id": raster_id.strip(),
        "sha256": digest.lower(),
        "width_px": width,
        "height_px": height,
        "source_date": source_date,
        "sample_kind": sample_kind,
    }


def _validate_points(
    control_points: Sequence[Mapping[str, Any]], width: int, height: int, policy: CalibrationPolicy
) -> list[dict[str, Any]]:
    if isinstance(control_points, (str, bytes)) or not isinstance(control_points, Sequence):
        raise RasterEvidenceError("control_points must be a bounded sequence")
    if not 3 <= len(control_points) <= policy.max_control_points:
        raise RasterEvidenceError(f"at least three and at most {policy.max_control_points} control points are required")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    pixel_locations: set[tuple[float, float]] = set()
    for raw in control_points:
        if not isinstance(raw, Mapping):
            raise RasterEvidenceError("each control point must be a mapping")
        point_id = raw.get("point_id")
        if not isinstance(point_id, str) or not point_id.strip() or len(point_id) > 80:
            raise RasterEvidenceError("point_id must be a non-empty bounded string")
        point_id = point_id.strip()
        if point_id in ids:
            raise RasterEvidenceError("control point identifiers must be unique")
        ids.add(point_id)
        x = _number(raw.get("pixel_x"), "pixel_x")
        y = _number(raw.get("pixel_y"), "pixel_y")
        longitude = _number(raw.get("longitude"), "longitude")
        latitude = _number(raw.get("latitude"), "latitude")
        if not 0 <= x <= width - 1 or not 0 <= y <= height - 1:
            raise RasterEvidenceError("pixel coordinates must lie inside the raster bounds")
        if not -180 <= longitude <= 180:
            raise RasterEvidenceError("longitude must be within WGS84 axis bounds [-180, 180]")
        if not -90 <= latitude <= 90:
            raise RasterEvidenceError("latitude must be within WGS84 axis bounds [-90, 90]")
        pixel = (x, y)
        if pixel in pixel_locations:
            raise RasterEvidenceError("control points must use distinct pixel coordinates")
        pixel_locations.add(pixel)
        normalized.append(
            {
                "point_id": point_id,
                "pixel_x": x,
                "pixel_y": y,
                "longitude": longitude,
                "latitude": latitude,
            }
        )
    if max(point["longitude"] for point in normalized) - min(point["longitude"] for point in normalized) > 180:
        raise RasterEvidenceError("antimeridian-spanning control points are unsupported")
    return sorted(normalized, key=lambda point: point["point_id"])


def _gate_reasons(
    rights_status: str,
    rights_reference: str | None,
    policy: CalibrationPolicy,
    rms_residual: float,
    scales: Mapping[str, float],
) -> list[str]:
    reasons: list[str] = []
    if not policy.enabled or not policy.policy_id:
        reasons.append("owner_policy_disabled")
    if rights_status != "permission_recorded":
        reasons.append(f"rights_{rights_status}")
    elif not rights_reference or rights_reference not in policy.approved_rights_references:
        reasons.append("rights_reference_not_approved")
    if not policy.independent_review_reference:
        reasons.append("independent_review_pending")
    if (
        policy.max_residual_m is None
        or policy.min_scale_m_per_pixel is None
        or policy.max_scale_m_per_pixel is None
    ):
        reasons.append("calibration_limits_missing")
    else:
        if rms_residual > policy.max_residual_m:
            reasons.append("residual_exceeds_policy")
        if any(
            value < policy.min_scale_m_per_pixel or value > policy.max_scale_m_per_pixel
            for value in scales.values()
        ):
            reasons.append("scale_outside_policy")
    return reasons


def build_calibration_receipt(
    raster: Mapping[str, Any],
    control_points: Sequence[Mapping[str, Any]],
    *,
    rights_status: str = "unknown",
    rights_reference: str | None = None,
    policy: CalibrationPolicy | None = None,
) -> dict[str, Any]:
    """Fit pixel-to-WGS84 affine coefficients and return a hash-bound receipt.

    This computes evidence only. The default policy can never enable comparison;
    a caller must supply owner rights, limits, and independent-review references.
    """

    active_policy = policy or CalibrationPolicy()
    _validate_policy(active_policy)
    if not isinstance(rights_status, str) or rights_status not in _RIGHTS_STATUSES:
        raise RasterEvidenceError("rights_status must be unknown, restricted, or permission_recorded")
    if rights_reference is not None and (
        not isinstance(rights_reference, str) or not rights_reference.strip() or len(rights_reference) > 200
    ):
        raise RasterEvidenceError("rights_reference must be a bounded non-empty string or null")
    if rights_reference is not None:
        rights_reference = rights_reference.strip()
    if rights_status == "permission_recorded" and not rights_reference:
        raise RasterEvidenceError("permission_recorded rights require a rights_reference")

    raster_data = _validate_raster(raster, active_policy)
    points = _validate_points(
        control_points,
        raster_data["width_px"],
        raster_data["height_px"],
        active_policy,
    )
    center_x = sum(point["pixel_x"] for point in points) / len(points)
    center_y = sum(point["pixel_y"] for point in points) / len(points)
    longitude = _fit_axis(points, "longitude", center_x, center_y)
    latitude = _fit_axis(points, "latitude", center_x, center_y)
    matrix = {"longitude": list(longitude), "latitude": list(latitude)}

    for x, y in (
        (0, 0),
        (raster_data["width_px"] - 1, 0),
        (0, raster_data["height_px"] - 1),
        (raster_data["width_px"] - 1, raster_data["height_px"] - 1),
    ):
        lon = _apply(longitude, x, y)
        lat = _apply(latitude, x, y)
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise RasterEvidenceError("fitted raster corners exceed WGS84 longitude/latitude bounds")

    residuals: list[dict[str, Any]] = []
    for point in points:
        fitted_lon = _apply(longitude, point["pixel_x"], point["pixel_y"])
        fitted_lat = _apply(latitude, point["pixel_x"], point["pixel_y"])
        residuals.append(
            {
                "point_id": point["point_id"],
                "residual_m": _haversine_m(
                    point["longitude"], point["latitude"], fitted_lon, fitted_lat
                ),
            }
        )
    errors = [row["residual_m"] for row in residuals]
    rms = math.sqrt(sum(error * error for error in errors) / len(errors))
    maximum = max(errors)
    center_lon = _apply(longitude, center_x, center_y)
    center_lat = _apply(latitude, center_x, center_y)
    scale_x = _haversine_m(
        center_lon,
        center_lat,
        _apply(longitude, center_x + 1, center_y),
        _apply(latitude, center_x + 1, center_y),
    )
    scale_y = _haversine_m(
        center_lon,
        center_lat,
        _apply(longitude, center_x, center_y + 1),
        _apply(latitude, center_x, center_y + 1),
    )
    scales = {"x": scale_x, "y": scale_y}
    gate_reasons = _gate_reasons(rights_status, rights_reference, active_policy, rms, scales)

    receipt: dict[str, Any] = {
        "receipt_version": 1,
        "sample_kind": raster_data["sample_kind"],
        "raster": raster_data,
        "rights_status": rights_status,
        "rights_reference": rights_reference,
        "policy_id": active_policy.policy_id,
        "independent_review_status": (
            "reference_supplied_not_independently_verified"
            if active_policy.independent_review_reference
            else "pending"
        ),
        "independent_review_reference": active_policy.independent_review_reference,
        "control_points": points,
        "affine_wgs84": matrix,
        "residuals_m": residuals,
        "residual_summary_m": {"rms": rms, "max": maximum},
        "scale_m_per_pixel": scales,
        "calibration_limits": {
            "max_control_points": active_policy.max_control_points,
            "max_raster_pixels": active_policy.max_raster_pixels,
            "max_residual_m": active_policy.max_residual_m,
            "min_scale_m_per_pixel": active_policy.min_scale_m_per_pixel,
            "max_scale_m_per_pixel": active_policy.max_scale_m_per_pixel,
            "projection": _PROJECTION,
        },
        "gate_reasons": gate_reasons,
        "eligible_for_comparison": not gate_reasons,
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def validate_calibration_receipt(
    receipt: Mapping[str, Any], *, policy: CalibrationPolicy | None = None
) -> dict[str, Any]:
    """Recalculate every fit field and verify its canonical receipt digest."""

    try:
        if not isinstance(receipt, Mapping):
            raise RasterEvidenceError("receipt must be a mapping")
        expected = build_calibration_receipt(
            receipt["raster"],
            receipt["control_points"],
            rights_status=receipt["rights_status"],
            rights_reference=receipt.get("rights_reference"),
            policy=policy,
        )
        if _canonical_bytes(dict(receipt)) != _canonical_bytes(expected):
            return {
                "valid": False,
                "eligible_for_comparison": False,
                "errors": ["receipt_content_or_digest_mismatch"],
            }
        return {
            "valid": True,
            "eligible_for_comparison": expected["eligible_for_comparison"],
            "errors": [],
        }
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {
            "valid": False,
            "eligible_for_comparison": False,
            "errors": [str(exc)[:240] or "invalid_receipt"],
        }
