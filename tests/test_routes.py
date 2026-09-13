from app.routes import build_gpx, normalize_ors_response
import pytest


def test_build_gpx_contains_trackpoints_and_escapes_route_name():
    gpx = build_gpx(
        "Leira & Lade",
        [(10.4, 63.4), (10.5, 63.5)],
    )

    assert "<gpx" in gpx
    assert "Leira &amp; Lade" in gpx
    assert gpx.count("<trkpt") == 2
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
