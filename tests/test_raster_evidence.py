from __future__ import annotations

import copy
import hashlib

import pytest

from app.raster_evidence import (
    CalibrationPolicy,
    RasterEvidenceError,
    build_calibration_receipt,
    validate_calibration_receipt,
)


RASTER = {
    "raster_id": "synthetic:grid-1",
    "sha256": hashlib.sha256(b"synthetic raster bytes; no image supplied").hexdigest(),
    "width_px": 1001,
    "height_px": 801,
    "source_date": None,
}


def point(point_id: str, x: float, y: float, lon: float, lat: float) -> dict[str, object]:
    return {
        "point_id": point_id,
        "pixel_x": x,
        "pixel_y": y,
        "longitude": lon,
        "latitude": lat,
    }


def controls() -> list[dict[str, object]]:
    # Four synthetic controls with a small non-affine perturbation make the
    # fitted matrix and residual receipt change when one point is removed.
    return [
        point("a", 0, 0, 10.0, 63.0),
        point("b", 1000, 0, 10.0101, 63.0001),
        point("c", 0, 800, 9.9999, 62.9921),
        point("d", 1000, 800, 10.0104, 62.9918),
    ]


def test_default_policy_records_synthetic_calibration_without_enabling_use() -> None:
    receipt = build_calibration_receipt(
        RASTER,
        controls(),
        rights_status="unknown",
    )

    assert receipt["sample_kind"] == "synthetic"
    assert receipt["rights_status"] == "unknown"
    assert receipt["eligible_for_comparison"] is False
    assert "owner_policy_disabled" in receipt["gate_reasons"]
    assert len(receipt["control_points"]) == 4
    assert receipt["scale_m_per_pixel"]["x"] > 0
    assert receipt["scale_m_per_pixel"]["y"] > 0
    assert receipt["calibration_limits"]["max_residual_m"] is None
    assert "annotations" not in receipt
    assert "approach" not in receipt
    assert "access" not in receipt


@pytest.mark.parametrize("rights_status", ["unknown", "restricted", "permission_recorded"])
def test_rights_states_are_explicit_and_default_remains_disabled(rights_status: str) -> None:
    receipt = build_calibration_receipt(
        RASTER,
        controls(),
        rights_status=rights_status,
        rights_reference="synthetic-rights-ref" if rights_status == "permission_recorded" else None,
    )

    assert receipt["rights_status"] == rights_status
    assert receipt["eligible_for_comparison"] is False


def test_owner_policy_needs_recorded_rights_review_limits_and_independent_review() -> None:
    policy = CalibrationPolicy(
        enabled=True,
        policy_id="synthetic-owner-policy",
        approved_rights_references=("synthetic-rights-ref",),
        independent_review_reference="synthetic-independent-review",
        max_residual_m=100.0,
        min_scale_m_per_pixel=0.01,
        max_scale_m_per_pixel=1000.0,
    )
    receipt = build_calibration_receipt(
        RASTER,
        controls(),
        rights_status="permission_recorded",
        rights_reference="synthetic-rights-ref",
        policy=policy,
    )

    assert receipt["eligible_for_comparison"] is True
    assert receipt["independent_review_status"] == "reference_supplied_not_independently_verified"
    assert receipt["policy_id"] == "synthetic-owner-policy"
    assert validate_calibration_receipt(receipt, policy=policy)["valid"] is True


def test_restricted_rights_never_become_eligible_under_a_permissive_policy() -> None:
    policy = CalibrationPolicy(
        enabled=True,
        policy_id="synthetic-owner-policy",
        approved_rights_references=("synthetic-restriction-record",),
        independent_review_reference="synthetic-independent-review",
        max_residual_m=100.0,
        min_scale_m_per_pixel=0.01,
        max_scale_m_per_pixel=1000.0,
    )
    receipt = build_calibration_receipt(
        RASTER,
        controls(),
        rights_status="restricted",
        rights_reference="synthetic-restriction-record",
        policy=policy,
    )

    assert receipt["eligible_for_comparison"] is False
    assert "rights_restricted" in receipt["gate_reasons"]


def test_removing_control_point_changes_matrix_residuals_and_receipt_hash() -> None:
    full = build_calibration_receipt(RASTER, controls())
    reduced = build_calibration_receipt(RASTER, controls()[:-1])

    assert full["receipt_sha256"] != reduced["receipt_sha256"]
    assert full["affine_wgs84"] != reduced["affine_wgs84"]
    assert full["residuals_m"] != reduced["residuals_m"]


def test_receipt_validation_detects_tampering() -> None:
    receipt = build_calibration_receipt(RASTER, controls())
    altered = copy.deepcopy(receipt)
    altered["affine_wgs84"]["longitude"][0] += 0.5

    assert validate_calibration_receipt(receipt)["valid"] is True
    assert validate_calibration_receipt(altered)["valid"] is False


@pytest.mark.parametrize(
    "bad_point",
    [
        point("outside-lon", 1, 1, 180.0001, 63),
        point("outside-lat", 1, 1, 10, 90.0001),
        point("outside-pixel", 1001, 1, 10, 63),
    ],
)
def test_control_points_enforce_wgs84_axis_and_pixel_bounds(bad_point: dict[str, object]) -> None:
    with pytest.raises(RasterEvidenceError):
        build_calibration_receipt(RASTER, controls()[:3] + [bad_point])


def test_calibration_requires_three_non_collinear_unique_points() -> None:
    with pytest.raises(RasterEvidenceError, match="three"):
        build_calibration_receipt(RASTER, controls()[:2])

    collinear = [
        point("a", 0, 0, 10, 63),
        point("b", 10, 10, 10.001, 62.999),
        point("c", 20, 20, 10.002, 62.998),
    ]
    with pytest.raises(RasterEvidenceError, match="collinear"):
        build_calibration_receipt(RASTER, collinear)


def test_validation_keeps_computed_calibration_separate_from_acceptance_limits() -> None:
    receipt = build_calibration_receipt(RASTER, controls())
    result = validate_calibration_receipt(receipt)

    assert result == {
        "valid": True,
        "eligible_for_comparison": False,
        "errors": [],
    }
    assert receipt["residual_summary_m"]["rms"] >= 0
    assert receipt["calibration_limits"]["projection"] == "local affine WGS84 lon/lat; spherical residual estimate"
