# Route calculation contract

This document defines the pure route-planning and OpenRouteService normalization
boundary. It does not claim that a route is safe, accessible, permitted, current,
or fully traversable.

## Ordered route inputs

`routing_inputs(start, stops, mode, end)` accepts points with numeric `lat` and
`lon` fields. It rejects booleans, non-finite values, missing coordinates, and
latitude/longitude outside `[-90, 90]` and `[-180, 180]`. Provider coordinates
are returned in `(longitude, latitude)` order. At least one and at most 20
catalogue stops are accepted; labels are at most 200 characters.

The modes are:

- `one_way`: start followed by the ordered catalogue stops; an explicit end is
  rejected.
- `return_to_start`: start, ordered catalogue stops, and the actual start once
  as the terminal routing endpoint. That endpoint adds no catalogue stop.
- `explicit_end`: start, ordered catalogue stops, and a required user-provided
  endpoint. The endpoint adds no catalogue stop, including when its coordinates
  equal the start.

These inputs and the selected profile belong in the caller's immutable route
draft/request identity. Changing mode, endpoint, stop order/coordinates, or
profile therefore changes the semantic hash. Labels and manual visit durations
do not change provider request identity. GPX generation must use the same ordered
provider coordinates and endpoint labels; export-only longitude normalization
must not rewrite stored route evidence.

## Provider profile and response

`foot-hiking` is the default profile. The provider adapter recognizes only
`foot-hiking` and `foot-walking`; the application settings/controller must keep
the alternate profile unavailable until an owner-selected setting explicitly
enables it. The current route request path continues to use the hiking default.
The profile identifies provider configuration only; it does not establish
accessibility, safety, permission, or route suitability.
The response and profile shape were checked against the [official OpenRouteService
API v2 Swagger specification](https://github.com/GIScience/openrouteservice-docs/blob/master/API%20V2/swagger.json).

When OpenRouteService returns `properties.segments`, every segment must have
finite, non-boolean, non-negative distance and duration values. At most 21 legs
are retained, each bounded to 10,000,000 metres and seven days. When the
requested waypoint count is known, there must be exactly one fewer segment than
waypoints. Leg order is preserved. Segment distance and duration sums must each
reconcile with the route summary within `max(1 unit, 0.1% of the route summary)`;
the provider may round segment and route totals independently. Invalid or
unreconciled supplied segments reject the provider response. When `segments` is
absent, `leg_summaries` is `None` and travel by leg is unavailable. The existing
provider waypoint indices remain strictly increasing and in bounds.

## Calculation receipt and identity

The receipt contains provider (`openrouteservice`), profile, SHA-256 semantic
request hash, normalizer version, UTC calculation time, and only these bounded
engine metadata fields: version, build date, and graph date. Absent or unsafe
values are recorded as `unknown`. Raw headers, credentials, provider query
metadata, and coordinates are not retained in the receipt or emitted by these
helpers. The hash is an identifier for normalized inputs, not a promise that a
future provider call will reproduce the same geometry or metrics. Older saved
routes without a receipt remain a caller-level `missing provenance` case.

The semantic hash includes provider, profile, route mode, ordered provider
coordinates, and normalizer version. It excludes display labels and visit-time
budgets. A manual visit or declared-budget edit can therefore recompute budget
presentation without implying that route geometry was recalculated.

## Visit-time and budget calculation

The pure `assess_route_budget` helper combines retained provider leg durations
with explicit per-catalogue-stop planned visit minutes. It accepts at most 20
visits, each from 0 through 1,440 minutes, and a declared budget from 0 through
10,080 minutes. Fractional minute values are retained. `None` is allowed for a
visit whose planned duration has not been entered; known visits are summed, but
the total itinerary and budget comparison remain unavailable until every visit
has an explicit value.

If leg summaries are absent, travel and the combined itinerary total remain
unavailable regardless of the aggregate provider duration. The helper never
divides aggregate duration among stops. Changing a visit or budget value is a
local calculation and performs no provider request. Planned visit duration is
an intention, not recorded dwell time, a deadline estimate, or a guarantee.

## Integration boundary

The route request/controller, database snapshot and migration, settings gate for
`foot-walking`, frontend editor, GPX endpoint agreement, and legacy-route
presentation are owned by the parent slice. They must bind the helper output to
the same immutable draft/saved route before the complete BK55–57 acceptance
targets can be claimed.
