from pathlib import Path
import hashlib
import shutil
import subprocess
import xml.etree.ElementTree as ET

from app.routes import build_gpx, normalize_ors_response
import pytest


def test_build_gpx_contains_trackpoints_and_escapes_route_name():
    gpx = build_gpx(
        "Leira & Lade",
        [(10.4, 63.4), (10.5, 63.5)],
        [(10.4, 63.4, "Start"), (10.5, 63.5, "Bunker & cave")],
    )

    assert "<gpx" in gpx
    assert "Leira &amp; Lade" in gpx
    assert gpx.count("<trkpt") == 2
    assert gpx.count("<wpt") == 2
    assert "Bunker &amp; cave" in gpx
    assert 'lat="63.4000000" lon="10.4000000"' in gpx


def test_normalize_ors_response_extracts_summary_and_coordinates():
    route = normalize_ors_response(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[10.4, 63.4], [10.5, 63.5]],
                    },
                    "properties": {
                        "summary": {"distance": 1200, "duration": 900}
                    },
                }
            ],
        }
    )

    assert route.distance_m == 1200
    assert route.duration_s == 900
    assert route.coordinates == [(10.4, 63.4), (10.5, 63.5)]


@pytest.mark.parametrize(
    "payload",
    [[], {}, {"features": [None]}, {"features": [{"geometry": {"type": "LineString", "coordinates": []}}]}],
)
def test_malformed_provider_payload_is_a_controlled_value_error(payload):
    with pytest.raises(ValueError):
        normalize_ors_response(payload)


@pytest.mark.parametrize("text", ["bad\x00label", "bad\x01label", "bad\x0blabel", "bad\x1flabel"])
def test_build_gpx_rejects_illegal_xml_10_control_characters(text):
    with pytest.raises(ValueError, match="XML"):
        build_gpx(text, [(10.0, 63.0), (10.1, 63.1)])


def test_build_gpx_rejects_illegal_controls_in_waypoint_labels():
    with pytest.raises(ValueError, match="XML"):
        build_gpx("Route", [(10.0, 63.0), (10.1, 63.1)], [(10.0, 63.0, "Start\x00")])


@pytest.mark.parametrize(
    "coordinate",
    [
        (float("nan"), 63.0),
        (10.0, float("inf")),
        (True, 63.0),
        (10.0, False),
        (10**1000, 63.0),
        (180.000001, 63.0),
        (10.0, 90.000001),
    ],
)
def test_build_gpx_rejects_non_finite_boolean_and_out_of_range_coordinates(coordinate):
    with pytest.raises(ValueError, match="coordinate"):
        build_gpx("Route", [coordinate, (10.1, 63.1)])


def test_build_gpx_normalizes_positive_180_only_in_exported_gpx():
    coordinates = [(180, 0), (-180, 1)]
    waypoints = [(180, 0, "Edge")]
    gpx = build_gpx("Boundary", coordinates, waypoints)
    root = ET.fromstring(gpx)
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}

    assert [point.attrib["lon"] for point in root.findall(".//gpx:trkpt", ns)] == [
        "-180.0000000",
        "-180.0000000",
    ]
    assert root.find("gpx:wpt", ns).attrib["lon"] == "-180.0000000"
    assert coordinates == [(180, 0), (-180, 1)]
    assert waypoints == [(180, 0, "Edge")]


def test_build_gpx_validates_against_pinned_official_gpx_11_schema(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "gpx" / "gpx-1.1.xsd"
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == (
        "9e4d1988b862edbe556305b130f8f6f1b29864fefd0dc02d5dab04ccdd1f34d6"
    )
    validator = shutil.which("xmllint")
    if validator is None:
        pytest.skip("xmllint is required for GPX 1.1 XSD validation")
    path = tmp_path / "route.gpx"
    path.write_text(
        build_gpx(
            "Æø & <route>",
            [(10.0, 63.0), (10.1, 63.1)],
            [(10.0, 63.0, "Start & café")],
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [validator, "--noout", "--schema", str(fixture), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
