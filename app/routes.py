from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import math
from urllib.request import Request, urlopen


ORS_URL = "https://api.openrouteservice.org/v2/directions/foot-hiking/geojson"


@dataclass(frozen=True)
class RouteResult:
    distance_m: float
    duration_s: float
    coordinates: list[tuple[float, float]]


def normalize_ors_response(payload: dict) -> RouteResult:
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
    summary = properties.get("summary", {}) if isinstance(properties, dict) else {}
    try:
        distance_m = float(summary["distance"])
        duration_s = float(summary["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("routing provider returned invalid summary") from error
    if not math.isfinite(distance_m) or not math.isfinite(duration_s):
        raise ValueError("routing provider returned non-finite summary values")
    if distance_m < 0 or duration_s < 0:
        raise ValueError("routing provider returned negative summary values")
    return RouteResult(distance_m, duration_s, normalized)


def fetch_openrouteservice(
    api_key: str, coordinates: list[tuple[float, float]], timeout: float = 20
) -> RouteResult:
    body = json.dumps({"coordinates": coordinates, "instructions": False}).encode()
    request = Request(
        ORS_URL,
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
    return normalize_ors_response(json.loads(raw))


def build_gpx(name: str, coordinates: list[tuple[float, float]]) -> str:
    if len(coordinates) < 2:
        raise ValueError("a GPX track requires at least two points")
    trackpoints = "".join(
        f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}" />' for lon, lat in coordinates
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Bunkerkartet" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>{escape(name)}</name><trkseg>{trackpoints}</trkseg></trk>"
        "</gpx>\n"
    )
