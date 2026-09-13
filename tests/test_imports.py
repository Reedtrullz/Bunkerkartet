from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.imports import ImportPackage, validate_import_package


def source():
    return {
        "url": "https://example.com/trondheim-site",
        "title": "Trondheim site record",
        "source_type": "web",
        "excerpt": "A short source excerpt describing the location.",
    }


def record(**overrides):
    value = {
        "external_key": "example:site-1",
        "name": "Example fort",
        "site_kind": "fortress",
        "geometry": {"latitude": 63.4305, "longitude": 10.3951},
        "precision": "approximate",
        "uncertainty_m": 150,
        "location_basis": "map_reference",
        "status": "candidate",
        "access": "unknown",
        "sources": [source()],
    }
    value.update(overrides)
    return value


def package(*records):
    return {
        "schema_version": "1.0",
        "batch_id": "trondheim-2026-09-13-001",
        "generated_at": "2026-09-13T12:00:00Z",
        "records": list(records or [record()]),
    }


def test_valid_package_is_parsed_with_candidate_status_and_unknown_access():
    parsed = validate_import_package(package())

    assert isinstance(parsed, ImportPackage)
    assert parsed.records[0].status == "candidate"
    assert parsed.records[0].access == "unknown"
    assert parsed.generated_at == datetime(2026, 9, 13, 12, tzinfo=timezone.utc)


def test_missing_sources_rejects_record_without_provenance():
    with pytest.raises(ValidationError):
        validate_import_package(package(record(sources=[])))


def test_malformed_source_url_is_rejected():
    with pytest.raises(ValidationError):
        validate_import_package(package(record(sources=[{**source(), "url": "trondheim-site"}])))


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(91, 10), (63, 181), ("63.4", 10)],
)
def test_invalid_coordinates_are_rejected(latitude, longitude):
    with pytest.raises(ValidationError):
        validate_import_package(
            package(record(geometry={"latitude": latitude, "longitude": longitude}))
        )


def test_geometry_is_required_unless_precision_is_unknown():
    with pytest.raises(ValidationError):
        validate_import_package(package(record(geometry=None, precision="approximate")))

    parsed = validate_import_package(package(record(geometry=None, precision="unknown")))
    assert parsed.records[0].geometry is None


@pytest.mark.parametrize("field", ["name", "site_kind"])
def test_snublestein_records_are_rejected(field):
    with pytest.raises(ValidationError):
        validate_import_package(package(record(**{field: "Snublestein Trondheim"})))


def test_approximate_uncertainty_is_preserved():
    parsed = validate_import_package(package(record(uncertainty_m=875.5)))

    assert parsed.records[0].uncertainty_m == 875.5


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        validate_import_package(package(record(unreviewed_chain_of_thought="secret")))
