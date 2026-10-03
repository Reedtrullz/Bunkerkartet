"""Pure route-input, provenance, and itinerary-budget helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Literal


DEFAULT_PROFILE = "foot-hiking"
SUPPORTED_PROFILES = frozenset({"foot-hiking", "foot-walking"})
ROUTE_NORMALIZER_VERSION = "ors-geojson-v1"
MAX_ROUTE_STOPS = 20
MAX_ROUTE_POINTS = MAX_ROUTE_STOPS + 2
MAX_ROUTE_LABEL_LENGTH = 200
MAX_PROVIDER_LEGS = MAX_ROUTE_POINTS - 1
MAX_PROVIDER_LEG_DISTANCE_M = 10_000_000.0
MAX_PROVIDER_LEG_DURATION_S = 7 * 24 * 60 * 60
MAX_VISIT_MINUTES_PER_STOP = 24 * 60
MAX_DECLARED_BUDGET_MINUTES = 7 * 24 * 60

RouteMode = Literal["one_way", "return_to_start", "explicit_end"]
Coordinate = tuple[float, float]  # OpenRouteService order: longitude, latitude.


@dataclass(frozen=True)
class RoutingInputs:
    coordinates: tuple[Coordinate, ...]
    labels: tuple[str, ...]
    mode: RouteMode
    catalogue_stop_count: int


@dataclass(frozen=True)
class ProviderLeg:
    distance_m: float
    duration_s: float


@dataclass(frozen=True)
class EngineMetadata:
    version: str = "unknown"
    build_date: str = "unknown"
    graph_date: str = "unknown"


@dataclass(frozen=True)
class CalculationReceipt:
    provider: str
    profile: str
    semantic_hash: str
    normalizer_version: str
    calculated_at: str
    engine_metadata: EngineMetadata

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "profile": self.profile,
            "semantic_hash": self.semantic_hash,
            "normalizer_version": self.normalizer_version,
            "calculated_at": self.calculated_at,
            "engine_metadata": asdict(self.engine_metadata),
        }


@dataclass(frozen=True)
class RouteBudgetAssessment:
    travel_minutes: float | None
    visit_minutes: float
    total_minutes: float | None
    declared_budget_minutes: float | None
    over_budget: bool | None
    travel_status: Literal["available", "unavailable"]
    visit_status: Literal["complete", "incomplete"]
    comparison_status: Literal["within_budget", "over_budget", "unavailable", "not_declared"]


def validate_profile(profile: object) -> str:
    if not isinstance(profile, str) or profile not in SUPPORTED_PROFILES:
        raise ValueError("unsupported route profile")
    return profile


def routing_inputs(
    start: object,
    stops: Sequence[object],
    mode: RouteMode = "one_way",
    end: object | None = None,
) -> RoutingInputs:
    """Validate ordered route points and return provider coordinates plus labels.

    Catalogue stops remain separate from an explicit or return-to-start endpoint.
    Input points use ``lat``/``lon`` fields; provider coordinates use ``lon``/``lat``.
    """
    if mode not in ("one_way", "return_to_start", "explicit_end"):
        raise ValueError("unsupported route mode")
    if not isinstance(stops, Sequence) or isinstance(stops, (str, bytes)):
        raise ValueError("route stops must be an ordered sequence")
    if not stops:
        raise ValueError("at least one catalogue stop is required")
    if len(stops) > MAX_ROUTE_STOPS:
        raise ValueError("too many route stops")
    if mode == "explicit_end" and end is None:
        raise ValueError("explicit_end mode requires an endpoint")
    if mode != "explicit_end" and end is not None:
        raise ValueError("endpoint is only valid in explicit_end mode")

    start_coordinate = _coordinate(start, "route start")
    coordinates = [start_coordinate]
    labels = [_point_label(start, "Route start")]
    for index, stop in enumerate(stops, start=1):
        coordinates.append(_coordinate(stop, f"route stop {index}"))
        labels.append(_point_label(stop, f"Stop {index}"))

    if mode == "return_to_start":
        coordinates.append(start_coordinate)
        labels.append("Return to start")
    elif mode == "explicit_end":
        coordinates.append(_coordinate(end, "route endpoint"))
        labels.append(_point_label(end, "Route end"))

    return RoutingInputs(tuple(coordinates), tuple(labels), mode, len(stops))


def semantic_request_hash(inputs: RoutingInputs, *, profile: str = DEFAULT_PROFILE) -> str:
    """Hash provider-affecting semantics without returning private coordinates."""
    if not isinstance(inputs, RoutingInputs):
        raise ValueError("routing inputs are required")
    profile = validate_profile(profile)
    if len(inputs.coordinates) < 2 or len(inputs.coordinates) > MAX_ROUTE_POINTS:
        raise ValueError("route point count is out of bounds")
    payload = {
        "provider": "openrouteservice",
        "profile": profile,
        "mode": inputs.mode,
        "coordinates": [[_canonical_zero(lon), _canonical_zero(lat)] for lon, lat in inputs.coordinates],
        "normalizer_version": ROUTE_NORMALIZER_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_calculation_receipt(
    inputs: RoutingInputs,
    *,
    profile: str = DEFAULT_PROFILE,
    engine_metadata: Mapping[str, object] | EngineMetadata | None = None,
    calculated_at: datetime | None = None,
) -> CalculationReceipt:
    profile = validate_profile(profile)
    instant = calculated_at or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("calculation time must include a timezone")
    timestamp = instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return CalculationReceipt(
        provider="openrouteservice",
        profile=profile,
        semantic_hash=semantic_request_hash(inputs, profile=profile),
        normalizer_version=ROUTE_NORMALIZER_VERSION,
        calculated_at=timestamp,
        engine_metadata=safe_engine_metadata(engine_metadata),
    )


def safe_engine_metadata(value: Mapping[str, object] | EngineMetadata | None) -> EngineMetadata:
    """Keep only short, printable version/date strings from provider engine data."""
    if isinstance(value, EngineMetadata):
        fields: Mapping[str, object] = {
            "version": value.version,
            "build_date": value.build_date,
            "graph_date": value.graph_date,
        }
    elif isinstance(value, Mapping):
        fields = value
    else:
        fields = {}
    return EngineMetadata(
        version=_safe_engine_field(fields.get("version")),
        build_date=_safe_engine_field(fields.get("build_date")),
        graph_date=_safe_engine_field(fields.get("graph_date")),
    )


def assess_route_budget(
    legs: Sequence[ProviderLeg] | None,
    visit_minutes: Sequence[int | float | None],
    *,
    declared_budget_minutes: int | float | None = None,
) -> RouteBudgetAssessment:
    """Combine stored travel estimates with explicit planned visit durations.

    ``None`` legs means provider travel is unavailable. ``None`` visit entries
    remain unknown and are never filled from route totals or averages.
    """
    if not isinstance(visit_minutes, Sequence) or isinstance(visit_minutes, (str, bytes)):
        raise ValueError("visit durations must be an ordered sequence")
    if len(visit_minutes) > MAX_ROUTE_STOPS:
        raise ValueError("too many visit durations")
    visit_values: list[float | None] = [
        None if value is None else _bounded_minutes(value, "visit duration", MAX_VISIT_MINUTES_PER_STOP)
        for value in visit_minutes
    ]
    visit_complete = all(value is not None for value in visit_values)
    visit_total = sum(value for value in visit_values if value is not None)

    declared = (
        None
        if declared_budget_minutes is None
        else _bounded_minutes(declared_budget_minutes, "declared budget", MAX_DECLARED_BUDGET_MINUTES)
    )
    travel: float | None
    if legs is None:
        travel = None
    else:
        if not isinstance(legs, Sequence) or isinstance(legs, (str, bytes)):
            raise ValueError("provider legs must be an ordered sequence")
        if not legs or len(legs) > MAX_PROVIDER_LEGS:
            raise ValueError("provider leg count is out of bounds")
        durations: list[float] = []
        for leg in legs:
            if not isinstance(leg, ProviderLeg):
                raise ValueError("invalid provider leg summary")
            _bounded_provider_value(leg.distance_m, MAX_PROVIDER_LEG_DISTANCE_M, "leg distance")
            durations.append(_bounded_provider_value(leg.duration_s, MAX_PROVIDER_LEG_DURATION_S, "leg duration"))
        travel = sum(durations) / 60

    total = travel + visit_total if travel is not None and visit_complete else None
    over_budget = None if total is None or declared is None else total > declared
    if declared is None:
        comparison = "not_declared"
    elif over_budget is None:
        comparison = "unavailable"
    else:
        comparison = "over_budget" if over_budget else "within_budget"
    return RouteBudgetAssessment(
        travel_minutes=travel,
        visit_minutes=visit_total,
        total_minutes=total,
        declared_budget_minutes=declared,
        over_budget=over_budget,
        travel_status="unavailable" if travel is None else "available",
        visit_status="complete" if visit_complete else "incomplete",
        comparison_status=comparison,
    )


def _coordinate(point: object, description: str) -> Coordinate:
    lat, lon = _field(point, "lat"), _field(point, "lon")
    latitude = _bounded_coordinate(lat, -90, 90, f"{description} latitude")
    longitude = _bounded_coordinate(lon, -180, 180, f"{description} longitude")
    return _canonical_zero(longitude), _canonical_zero(latitude)


def _field(point: object, name: str) -> object:
    if isinstance(point, Mapping):
        return point.get(name)
    return getattr(point, name, None)


def _point_label(point: object, fallback: str) -> str:
    label = _field(point, "label")
    if label is None:
        label = _field(point, "name")
    if label is None:
        return fallback
    if not isinstance(label, str) or not label.strip() or len(label) > MAX_ROUTE_LABEL_LENGTH:
        raise ValueError("route point label is invalid")
    return label


def _bounded_coordinate(value: object, minimum: float, maximum: float, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be numeric")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{description} is invalid") from error
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{description} is out of range")
    return result


def _canonical_zero(value: float) -> float:
    return 0.0 if value == 0 else value


def _safe_engine_field(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        return "unknown"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:+/()\-]{0,63}", value) is None:
        return "unknown"
    return value


def _bounded_minutes(value: object, description: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be numeric")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{description} is invalid") from error
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError(f"{description} is out of range")
    return result


def _bounded_provider_value(value: object, maximum: float, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be numeric")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{description} is invalid") from error
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError(f"{description} is out of range")
    return result
