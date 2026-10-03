from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

from app.route_planning import (
    DEFAULT_PROFILE,
    MAX_PROVIDER_LEG_DISTANCE_M,
    MAX_PROVIDER_LEG_DURATION_S,
    MAX_PROVIDER_LEGS,
    MAX_ROUTE_POINTS,
    ROUTE_NORMALIZER_VERSION,
    EngineMetadata,
    ProviderLeg,
    safe_engine_metadata,
    validate_profile,
)

ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-hiking/geojson"
ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions"
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


@dataclass(frozen=True)
class RouteResult:
    distance_m: float
    duration_s: float
    coordinates: list[tuple[float, float]]
    waypoint_indices: list[int] | None = None
    leg_summaries: tuple[ProviderLeg, ...] | None = None
    profile: str = DEFAULT_PROFILE
    engine_metadata: EngineMetadata = field(default_factory=EngineMetadata)
    normalizer_version: str = ROUTE_NORMALIZER_VERSION


def normalize_ors_response(
    payload: dict,
    *,
    requested_waypoint_count: int | None = None,
    profile: str = DEFAULT_PROFILE,
) -> RouteResult:
    profile = validate_profile(profile)
    if requested_waypoint_count is not None and (
        isinstance(requested_waypoint_count, bool)
        or not isinstance(requested_waypoint_count, int)
        or not 2 <= requested_waypoint_count <= MAX_ROUTE_POINTS
    ):
        raise ValueError("requested route waypoint count is out of bounds")
    if not isinstance(payload, dict):
        raise ValueError("routing provider returned invalid JSON object")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("routing provider returned no route")
    feature = features[0]
    if not isinstance(feature, dict):
        raise ValueError("routing provider returned invalid feature")
    geometry = feature.get("geometry", {})
    if not isinstance(geometry, dict):
        raise ValueError("routing provider returned invalid geometry")
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "LineString" or not isinstance(coordinates, list):
        raise ValueError("routing provider returned invalid geometry")

    normalized: list[tuple[float, float]] = []
    for coordinate in coordinates:
        if not isinstance(coordinate, list) or len(coordinate) < 2:
            raise ValueError("routing provider returned invalid coordinate")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in coordinate[:2]):
            raise ValueError("routing provider returned invalid coordinate")
        try:
            lon, lat = float(coordinate[0]), float(coordinate[1])
        except (TypeError, ValueError) as error:
            raise ValueError("routing provider returned invalid coordinate") from error
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError("routing provider returned out-of-range coordinate")
        normalized.append((lon, lat))
    if len(normalized) < 2:
        raise ValueError("routing provider returned too few coordinates")

    properties = feature.get("properties", {})
    waypoint_indices = None
    if isinstance(properties, dict) and "way_points" in properties:
        raw_waypoint_indices = properties["way_points"]
        if (
            not isinstance(raw_waypoint_indices, list)
            or any(isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(normalized)
                   for index in raw_waypoint_indices)
        ):
            raise ValueError("routing provider returned invalid waypoint indices")
        if any(left >= right for left, right in zip(raw_waypoint_indices, raw_waypoint_indices[1:])):
            raise ValueError("routing provider returned unordered waypoint indices")
        if requested_waypoint_count is not None and len(raw_waypoint_indices) != requested_waypoint_count:
            raise ValueError("routing provider returned an unexpected waypoint count")
        waypoint_indices = raw_waypoint_indices
    summary = properties.get("summary", {}) if isinstance(properties, dict) else {}
    if not isinstance(summary, dict) or any(
        isinstance(summary.get(key), bool) or not isinstance(summary.get(key), (int, float))
        for key in ("distance", "duration")
    ):
        raise ValueError("routing provider returned invalid summary")
    try:
        distance_m = float(summary["distance"])
        duration_s = float(summary["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("routing provider returned invalid summary") from error
    if not math.isfinite(distance_m) or not math.isfinite(duration_s):
        raise ValueError("routing provider returned non-finite summary values")
    if distance_m < 0 or duration_s < 0:
        raise ValueError("routing provider returned negative summary values")

    leg_summaries = None
    if isinstance(properties, dict) and "segments" in properties:
        leg_summaries = _normalize_provider_legs(
            properties["segments"],
            requested_waypoint_count=requested_waypoint_count,
            total_distance_m=distance_m,
            total_duration_s=duration_s,
        )
    metadata = payload.get("metadata")
    engine = metadata.get("engine") if isinstance(metadata, dict) else None
    return RouteResult(
        distance_m,
        duration_s,
        normalized,
        waypoint_indices,
        leg_summaries,
        profile,
        safe_engine_metadata(engine),
        ROUTE_NORMALIZER_VERSION,
    )


def _normalize_provider_legs(
    raw_legs: object,
    *,
    requested_waypoint_count: int | None,
    total_distance_m: float,
    total_duration_s: float,
) -> tuple[ProviderLeg, ...]:
    if not isinstance(raw_legs, list) or not 1 <= len(raw_legs) <= MAX_PROVIDER_LEGS:
        raise ValueError("routing provider returned invalid leg summaries")
    expected_count = requested_waypoint_count - 1 if requested_waypoint_count is not None else len(raw_legs)
    if len(raw_legs) != expected_count:
        raise ValueError("routing provider returned an unexpected leg count")

    legs: list[ProviderLeg] = []
    for raw_leg in raw_legs:
        if not isinstance(raw_leg, dict):
            raise ValueError("routing provider returned an invalid leg")
        values = (raw_leg.get("distance"), raw_leg.get("duration"))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError("routing provider returned invalid leg metrics")
        try:
            distance_m, duration_s = float(values[0]), float(values[1])
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("routing provider returned invalid leg metrics") from error
        if (
            not math.isfinite(distance_m)
            or not math.isfinite(duration_s)
            or not 0 <= distance_m <= MAX_PROVIDER_LEG_DISTANCE_M
            or not 0 <= duration_s <= MAX_PROVIDER_LEG_DURATION_S
        ):
            raise ValueError("routing provider returned out-of-bounds leg metrics")
        legs.append(ProviderLeg(distance_m, duration_s))

    distance_sum = sum(leg.distance_m for leg in legs)
    duration_sum = sum(leg.duration_s for leg in legs)
    if not _provider_totals_reconcile(distance_sum, total_distance_m, abs_tol=1.0) or not _provider_totals_reconcile(
        duration_sum, total_duration_s, abs_tol=1.0
    ):
        raise ValueError("routing provider leg totals do not reconcile with route summary")
    return tuple(legs)


def _provider_totals_reconcile(legs_total: float, route_total: float, *, abs_tol: float) -> bool:
    # Provider totals may be rounded independently; allow max(1 unit, 0.1%).
    return abs(legs_total - route_total) <= max(abs_tol, 0.001 * abs(route_total))


def fetch_openrouteservice(
    api_key: str,
    coordinates: list[tuple[float, float]],
    timeout: float = 20,
    *,
    profile: str = DEFAULT_PROFILE,
) -> RouteResult:
    profile = validate_profile(profile)
    body = json.dumps({"coordinates": coordinates, "instructions": False}).encode()
    request = Request(
        f"{ORS_BASE_URL}/{profile}/geojson",
        data=body,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json, application/json",
            "User-Agent": "Bunkerkartet/0.1",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("routing provider response is too large")
    return normalize_ors_response(
        json.loads(raw),
        requested_waypoint_count=len(coordinates),
        profile=profile,
    )


def build_gpx(
    name: str,
    coordinates: list[tuple[float, float]],
    waypoints: list[tuple[float, float, str]] | None = None,
) -> str:
    if not isinstance(name, str):
        raise ValueError("route name must be text")
    validate_xml_text(name)
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        raise ValueError("a GPX track requires at least two points")
    normalized_coordinates = [_export_coordinate(pair) for pair in coordinates]
    normalized_waypoints: list[tuple[float, float, str]] = []
    for waypoint in waypoints or ():
        if not isinstance(waypoint, (list, tuple)) or len(waypoint) != 3:
            raise ValueError("invalid GPX waypoint")
        lon, lat = _export_coordinate(waypoint[:2])
        label = waypoint[2]
        validate_xml_text(label)
        normalized_waypoints.append((lon, lat, label))

    ET.register_namespace("", GPX_NAMESPACE)
    root = ET.Element(f"{{{GPX_NAMESPACE}}}gpx", {"version": "1.1", "creator": "Bunkerkartet"})
    for lon, lat, label in normalized_waypoints:
        point = ET.SubElement(
            root,
            f"{{{GPX_NAMESPACE}}}wpt",
            {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}"},
        )
        ET.SubElement(point, f"{{{GPX_NAMESPACE}}}name").text = label

    track = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}trk")
    ET.SubElement(track, f"{{{GPX_NAMESPACE}}}name").text = name
    segment = ET.SubElement(track, f"{{{GPX_NAMESPACE}}}trkseg")
    for lon, lat in normalized_coordinates:
        ET.SubElement(
            segment,
            f"{{{GPX_NAMESPACE}}}trkpt",
            {"lat": f"{lat:.7f}", "lon": f"{lon:.7f}"},
        )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def validate_xml_text(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("GPX labels must be text")
    for character in value:
        codepoint = ord(character)
        if not (
            codepoint in (0x9, 0xA, 0xD)
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            raise ValueError("GPX text contains an illegal XML 1.0 character")


def _export_coordinate(pair: object) -> tuple[float, float]:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError("invalid GPX coordinate")
    lon, lat = pair
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (lon, lat)):
        raise ValueError("invalid GPX coordinate")
    try:
        lon, lat = float(lon), float(lat)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("invalid GPX coordinate") from error
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError("GPX coordinate must be finite")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError("GPX coordinate is out of range")
    # GPX 1.1 defines longitude as [-180, 180); this affects export bytes only.
    return (-180.0 if lon == 180.0 else lon), lat


def qualify_stored_gpx(text: object) -> bool:
    """Fail closed on unavailable legacy GPX without changing its stored bytes."""
    if not isinstance(text,str) or len(text.encode('utf-8'))>2*1024*1024 or '<!DOCTYPE' in text.upper():return False
    try:
        validate_xml_text(text)
        root=ET.fromstring(text)
        if root.tag!=f'{{{GPX_NAMESPACE}}}gpx' or root.get('version')!='1.1' or not root.get('creator'):return False
        points=root.findall(f'.//{{{GPX_NAMESPACE}}}trkpt')
        if not points or len(points)>100000:return False
        for point in [*root.findall(f'{{{GPX_NAMESPACE}}}wpt'),*points]:
            lat=float(point.attrib['lat']);lon=float(point.attrib['lon'])
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<180:return False
        return True
    except (ET.ParseError,ValueError,KeyError,TypeError):return False
