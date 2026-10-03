from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib import import_module

import pytest

from app.routes import RouteResult, fetch_openrouteservice, normalize_ors_response


START = {"lat": 63.4, "lon": 10.4}
STOPS = [{"lat": 63.5, "lon": 10.5, "name": "Leira approach"}]


def _planning_api():
    try:
        return import_module("app.route_planning")
    except ModuleNotFoundError as error:
        if error.name != "app.route_planning":
            raise
        pytest.fail("route-planning helpers have not been implemented")


def routing_inputs(*args, **kwargs):
    return _planning_api().routing_inputs(*args, **kwargs)


def semantic_request_hash(*args, **kwargs):
    return _planning_api().semantic_request_hash(*args, **kwargs)


def build_calculation_receipt(*args, **kwargs):
    return _planning_api().build_calculation_receipt(*args, **kwargs)


def assess_route_budget(*args, **kwargs):
    return _planning_api().assess_route_budget(*args, **kwargs)


def ProviderLeg(*args, **kwargs):
    return _planning_api().ProviderLeg(*args, **kwargs)


def EngineMetadata(*args, **kwargs):
    return _planning_api().EngineMetadata(*args, **kwargs)


DEFAULT_PROFILE = "foot-hiking"


def test_one_way_routing_inputs_preserve_start_and_catalogue_stop_order():
    result = routing_inputs(START, STOPS)

    assert result.mode == "one_way"
    assert result.coordinates == ((10.4, 63.4), (10.5, 63.5))
    assert result.labels == ("Route start", "Leira approach")
    assert result.catalogue_stop_count == 1


def test_return_to_start_appends_start_as_endpoint_without_adding_a_catalogue_stop():
    result = routing_inputs(START, STOPS, mode="return_to_start")

    assert result.coordinates == ((10.4, 63.4), (10.5, 63.5), (10.4, 63.4))
    assert result.labels == ("Route start", "Leira approach", "Return to start")
    assert result.catalogue_stop_count == 1


def test_explicit_end_is_a_routing_endpoint_after_ordered_stops():
    result = routing_inputs(
        START,
        STOPS,
        mode="explicit_end",
        end={"lat": 63.6, "lon": 10.6, "label": "Meet point"},
    )

    assert result.coordinates[-1] == (10.6, 63.6)
    assert result.labels == ("Route start", "Leira approach", "Meet point")
    assert result.catalogue_stop_count == 1


def test_explicit_endpoint_equal_to_start_remains_an_endpoint_not_a_catalogue_stop():
    result = routing_inputs(START, STOPS, mode="explicit_end", end=START)

    assert result.coordinates == ((10.4, 63.4), (10.5, 63.5), (10.4, 63.4))
    assert result.catalogue_stop_count == 1


@pytest.mark.parametrize(
    ("mode", "end"),
    [("one_way", {"lat": 63.6, "lon": 10.6}), ("return_to_start", {"lat": 63.6, "lon": 10.6}), ("explicit_end", None)],
)
def test_route_modes_reject_missing_or_forbidden_endpoint(mode, end):
    with pytest.raises(ValueError):
        routing_inputs(START, STOPS, mode=mode, end=end)


@pytest.mark.parametrize("mode", ["round_trip", "", None])
def test_route_modes_reject_unknown_values(mode):
    with pytest.raises(ValueError):
        routing_inputs(START, STOPS, mode=mode)


@pytest.mark.parametrize(
    "bad_point",
    [
        {"lat": True, "lon": 10.4},
        {"lat": 63.4, "lon": False},
        {"lat": float("nan"), "lon": 10.4},
        {"lat": 63.4, "lon": float("inf")},
        {"lat": 90.0001, "lon": 10.4},
        {"lat": 63.4, "lon": 180.0001},
        {"lat": 63.4},
    ],
)
def test_routing_inputs_reject_boolean_nonfinite_and_out_of_range_coordinates(bad_point):
    with pytest.raises(ValueError):
        routing_inputs(bad_point, STOPS)


def test_semantic_request_hash_tracks_mode_endpoint_and_profile_without_serializing_coordinates():
    one_way = routing_inputs(START, STOPS)
    return_to_start = routing_inputs(START, STOPS, mode="return_to_start")
    explicit_end = routing_inputs(START, STOPS, mode="explicit_end", end={"lat": 63.6, "lon": 10.6})
    changed_end = routing_inputs(START, STOPS, mode="explicit_end", end={"lat": 63.61, "lon": 10.6})

    digests = {
        semantic_request_hash(one_way),
        semantic_request_hash(routing_inputs(START, STOPS)),
        semantic_request_hash(return_to_start),
        semantic_request_hash(explicit_end),
        semantic_request_hash(changed_end),
        semantic_request_hash(one_way, profile="foot-walking"),
    }
    receipt = build_calculation_receipt(
        one_way,
        calculated_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
    )

    assert len(digests) == 5
    assert receipt.provider == "openrouteservice"
    assert receipt.profile == DEFAULT_PROFILE == "foot-hiking"
    assert receipt.normalizer_version
    assert receipt.semantic_hash == semantic_request_hash(one_way)
    assert receipt.calculated_at == "2026-10-03T00:00:00Z"
    assert "63.4" not in json.dumps(receipt.to_dict())
    assert "10.4" not in json.dumps(receipt.to_dict())


def test_calculation_receipt_keeps_only_bounded_engine_metadata_and_explicit_unknowns():
    receipt = build_calculation_receipt(
        routing_inputs(START, STOPS),
        engine_metadata={
            "version": "9.2.1",
            "build_date": "2026-09-01",
            "graph_date": "2026-09-02",
            "authorization": "must not persist",
            "query": {"coordinates": [[10.4, 63.4]]},
        },
    )

    assert receipt.engine_metadata == EngineMetadata(
        version="9.2.1", build_date="2026-09-01", graph_date="2026-09-02"
    )
    assert "authorization" not in json.dumps(receipt.to_dict())
    assert "coordinates" not in json.dumps(receipt.to_dict())
    unknown = build_calculation_receipt(routing_inputs(START, STOPS)).engine_metadata
    assert unknown == EngineMetadata()


def test_provider_normalizer_retains_ordered_bounded_leg_summaries_and_engine_metadata():
    route = normalize_ors_response(
        {
            "metadata": {
                "engine": {
                    "version": "9.2.1",
                    "build_date": "2026-09-01",
                    "graph_date": "2026-09-02",
                    "raw_response": "do not retain",
                },
                "query": {"coordinates": [[10.4, 63.4]]},
            },
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[10.4, 63.4], [10.45, 63.45], [10.5, 63.5]],
                    },
                    "properties": {
                        "summary": {"distance": 1500, "duration": 1200},
                        "way_points": [0, 1, 2],
                        "segments": [
                            {"distance": 1000, "duration": 900},
                            {"distance": 500, "duration": 300},
                        ],
                    },
                }
            ],
        },
        requested_waypoint_count=3,
        profile="foot-walking",
    )

    assert route.profile == "foot-walking"
    assert route.waypoint_indices == [0, 1, 2]
    assert route.leg_summaries == (ProviderLeg(1000, 900), ProviderLeg(500, 300))
    assert route.engine_metadata == EngineMetadata("9.2.1", "2026-09-01", "2026-09-02")


def test_provider_normalizer_marks_absent_segment_summaries_unavailable():
    route = normalize_ors_response(
        {
            "features": [
                {
                    "geometry": {"type": "LineString", "coordinates": [[10, 63], [10.1, 63.1]]},
                    "properties": {"summary": {"distance": 1000, "duration": 600}},
                }
            ]
        },
        requested_waypoint_count=2,
    )

    assert route.leg_summaries is None
    assert route.engine_metadata == EngineMetadata()


def test_provider_leg_totals_allow_documented_rounding_tolerance():
    route = normalize_ors_response(
        {
            "features": [
                {
                    "geometry": {"type": "LineString", "coordinates": [[10, 63], [10.1, 63.1]]},
                    "properties": {
                        "summary": {"distance": 10_000, "duration": 10_000},
                        "segments": [{"distance": 10_009, "duration": 10_009}],
                    },
                }
            ]
        },
        requested_waypoint_count=2,
    )

    assert route.leg_summaries == (ProviderLeg(10_009, 10_009),)


def test_provider_leg_totals_outside_documented_rounding_tolerance_are_rejected():
    payload = {
        "features": [
            {
                "geometry": {"type": "LineString", "coordinates": [[10, 63], [10.1, 63.1]]},
                "properties": {
                    "summary": {"distance": 10_000, "duration": 10_000},
                    "segments": [{"distance": 10_010.005, "duration": 10_000}],
                },
            }
        ]
    }

    with pytest.raises(ValueError, match="reconcile"):
        normalize_ors_response(payload, requested_waypoint_count=2)


@pytest.mark.parametrize(
    "segments",
    [
        [{"distance": 1000, "duration": 900}],
        [{"distance": -1, "duration": 900}, {"distance": 500, "duration": 300}],
        [{"distance": True, "duration": 900}, {"distance": 500, "duration": 300}],
        [{"distance": float("inf"), "duration": 900}, {"distance": 500, "duration": 300}],
        [{"distance": 10_000_001, "duration": 900}, {"distance": 500, "duration": 300}],
        [{"distance": 1000, "duration": 800}, {"distance": 500, "duration": 300}],
    ],
)
def test_provider_rejects_missing_invalid_or_unreconciled_segment_summaries(segments):
    payload = {
        "features": [
            {
                "geometry": {"type": "LineString", "coordinates": [[10, 63], [10.1, 63.1], [10.2, 63.2]]},
                "properties": {
                    "summary": {"distance": 1500, "duration": 1200},
                    "segments": segments,
                },
            }
        ]
    }

    with pytest.raises(ValueError):
        normalize_ors_response(payload, requested_waypoint_count=3)


def test_existing_four_argument_route_result_construction_remains_compatible():
    route = RouteResult(1000, 600, [(10.0, 63.0), (10.1, 63.1)], [0, 1])

    assert route.leg_summaries is None
    assert route.profile == DEFAULT_PROFILE
    assert route.engine_metadata == EngineMetadata()


def test_provider_profile_selects_allowlisted_endpoint_and_rejects_unlisted_profile(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, limit):
            return json.dumps(
                {
                    "features": [
                        {
                            "geometry": {"type": "LineString", "coordinates": [[10, 63], [10.1, 63.1]]},
                            "properties": {"summary": {"distance": 100, "duration": 60}},
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, timeout):
        seen.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr("app.routes.urlopen", fake_urlopen)
    route = fetch_openrouteservice("key", [(10, 63), (10.1, 63.1)], profile="foot-walking")

    assert seen == [("https://api.openrouteservice.org/v2/directions/foot-walking/geojson", 20)]
    assert route.profile == "foot-walking"
    with pytest.raises(ValueError, match="profile"):
        fetch_openrouteservice("key", [(10, 63), (10.1, 63.1)], profile="cycling-road")


def test_budget_combines_validated_travel_with_separately_entered_visits():
    result = assess_route_budget(
        (ProviderLeg(1200, 900), ProviderLeg(300, 300)),
        (15, 0),
        declared_budget_minutes=40,
    )

    assert result.travel_minutes == 20
    assert result.visit_minutes == 15
    assert result.total_minutes == 35
    assert result.over_budget is False
    assert result.travel_status == "available"
    assert result.visit_status == "complete"


def test_budget_does_not_infer_missing_legs_or_incomplete_manual_visits():
    no_legs = assess_route_budget(None, (15, 5), declared_budget_minutes=60)
    incomplete_visits = assess_route_budget((ProviderLeg(1200, 900),), (15, None), declared_budget_minutes=60)

    assert no_legs.travel_minutes is None
    assert no_legs.total_minutes is None
    assert no_legs.over_budget is None
    assert no_legs.travel_status == "unavailable"
    assert no_legs.visit_status == "complete"
    assert incomplete_visits.travel_minutes == 15
    assert incomplete_visits.visit_minutes == 15
    assert incomplete_visits.total_minutes is None
    assert incomplete_visits.over_budget is None
    assert incomplete_visits.visit_status == "incomplete"


@pytest.mark.parametrize("value", [True, -1, 1441, float("inf")])
def test_budget_rejects_boolean_negative_and_unbounded_manual_minutes(value):
    with pytest.raises(ValueError):
        assess_route_budget((ProviderLeg(1200, 900),), (value,))


def test_budget_accepts_fractional_minutes_without_rounding_them():
    result = assess_route_budget((ProviderLeg(1200, 900),), (0.5,), declared_budget_minutes=15.5)

    assert result.visit_minutes == 0.5
    assert result.total_minutes == 15.5
    assert result.over_budget is False


def test_budget_rejects_declared_budget_above_bound():
    with pytest.raises(ValueError):
        assess_route_budget((ProviderLeg(1200, 900),), (0,), declared_budget_minutes=10081)
