from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.imports import ImportPackage, validate_import_package, validate_reference_url


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


def test_unsafe_access_is_a_valid_import_value():
    parsed = validate_import_package(package(record(access="unsafe")))

    assert parsed.records[0].access == "unsafe"


def test_json_source_dates_are_parsed_and_preserved():
    parsed = validate_import_package(
        package(
            record(
                sources=[
                    {
                        **source(),
                        "publication_date": "1944-01-01",
                        "access_date": "2026-09-13",
                    }
                ]
            )
        )
    )

    assert parsed.records[0].sources[0].publication_date == date(1944, 1, 1)
    assert parsed.records[0].sources[0].access_date == date(2026, 9, 13)


@pytest.mark.parametrize("generated_at", [123, {}, None])
def test_invalid_generated_at_types_are_validation_errors(generated_at):
    payload = package()
    payload["generated_at"] = generated_at
    with pytest.raises(ValidationError):
        validate_import_package(payload)


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

    parsed = validate_import_package(
        package(record(geometry=None, precision="unknown", uncertainty_m=None))
    )
    assert parsed.records[0].geometry is None

    missing_geometry = record(precision="unknown", uncertainty_m=None)
    missing_geometry.pop("geometry")
    parsed = validate_import_package(package(missing_geometry))
    assert parsed.records[0].geometry is None


@pytest.mark.parametrize("field", ["name", "site_kind"])
def test_snublestein_records_are_rejected(field):
    with pytest.raises(ValidationError):
        validate_import_package(package(record(**{field: "Snublestein Trondheim"})))


def test_approximate_uncertainty_is_preserved():
    parsed = validate_import_package(package(record(uncertainty_m=875.5)))

    assert parsed.records[0].uncertainty_m == 875.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"precision": "exact", "location_basis": "llm_inference"},
        {"precision": "approximate", "uncertainty_m": 0},
        {"geometry": None, "precision": "unknown", "uncertainty_m": 1},
    ],
)
def test_location_invariants_are_shared_by_import_validation(overrides):
    with pytest.raises(ValidationError):
        validate_import_package(package(record(**overrides)))


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        validate_import_package(package(record(unreviewed_chain_of_thought="secret")))


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.com/map",
        "https://example.com/map?api_key=synthetic-private-value",
        "https://example.com/map?X-AmZ-Signature=synthetic-private-value",
        "https://example.com/map?layer=https%3A%2F%2Fexample.org%2Fwms%3Ftoken%3Dsynthetic-private-value",
    ],
)
def test_reference_urls_reject_credential_like_values_without_echoing_input(url):
    with pytest.raises(ValueError) as error:
        validate_reference_url(url)

    assert "synthetic-private-value" not in str(error.value)


def test_reference_url_preserves_normal_map_parameters():
    url = "https://example.com/map?layer=roads&object_id=42&lat=63.4&lon=10.4"

    assert validate_reference_url(url) == url
