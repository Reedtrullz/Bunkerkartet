# Selected private exchange workflows

This document describes the independently mountable routes in
`app/exchange_workflows.py`. They implement BK-24 selected dossiers, BK-45
selected import subsets, and BK-51 revision-bound GIS coordinate review. The
parent application owns route installation, authentication, database schema,
and every write transaction.

## Installation seam

The parent calls:

```python
install_exchange_routes(
    app,
    database,
    admin_guard,
    site_detail,
    preview_package,
    commit_package,
    load_private_questions=load_private_questions,
    commit_location_edits=commit_location_edits,
)
```

The required synchronous callback signatures are:

```python
site_detail(connection, row) -> Mapping
preview_package(connection, package) -> Mapping  # includes native preview_hash
commit_package(database, package, native_preview_hash, provenance) -> Mapping
```

`site_detail` and `preview_package` are read-only. The import commit adapter must
persist the new batch and the supplied provenance in the same transaction as
the normal import commit. It must retain the existing import revision and
preview checks. It must not use the parent batch ID for the subset.

The optional private-question reader is
`load_private_questions(connection, site_ids, question_ids) -> Sequence[Mapping]`.
It is called on the same read transaction as selected site details and only
when question IDs are explicitly opted in. The optional GIS writer is
`commit_location_edits(database, edits, reason, preview_hash) -> Mapping`.
That adapter must recheck expected revisions and apply the existing native
location validation, revision increment, and location-review behavior
atomically. The exchange module itself issues no database writes.

Question-reader rows use the exact requested stable string `id`, integer
`site_id`, `prompt`, `state`, positive integer `revision`, and optional
`updated_at`; extra row fields are ignored. The GIS writer receives a list of
`{site_id, external_key, expected_revision, old, new}` mappings. `old` and
`new` are null or `[longitude, latitude]`, and unchanged features are omitted.

All routes are mounted under `/api/admin/exchanges` and use `admin_guard`.
Requests are strict JSON with duplicate-key rejection, a 2 MiB body limit, and
a 64-level nesting limit. JSON responses are capped at 4 MiB. The route layer
rejects unknown request fields; data records and callback output are separately
bounded. Dossier/site callback content and GIS features use allowlists; the
selected import workflow preserves the parent application's native preview
mapping under the fixed response-size limit.

## BK-24: selected dossier

`POST /dossiers/preview` accepts `site_ids` (1–20, unique), an optional title,
and three off-by-default sensitive selections:

- `include_private_start` with a complete `private_start` coordinate and label;
- `include_private_questions` with exact `question_ids`;
- `include_private_observations` with exact `observation_ids`.

The IDs and their matching opt-in flags must agree. Without an opt-in, the
personal start is null and private question/observation collections are empty;
the question callback is not called. Selected observations and questions are
read through explicit field allowlists. The dossier includes only the chosen
sites, bounded source links/excerpts and rights notes, bounded claims and
uncertainty, currentness, and an approach snapshot only when it has a recorded
review timestamp. A selected site's missing coordinate remains GeoJSON
`geometry: null`.

The response contains a version `1.0` JSON dossier, a printable HTML rendering,
selected-site GeoJSON, optional GPX, a `source_hash`, and a `preview_hash`.
The source hash covers the sanitized dossier snapshot; the preview hash binds
that snapshot and all rendered outputs.
HTML text and attributes are escaped. GPX contains labeled waypoints only; it
does not draw a line between sites or imply a calculated/walked path. Its status
is explicit when there are no known points or XML 1.0 cannot represent the
selected text. The JSON and printable output retain the generated timestamp,
currentness statement, and permission/access caveat. This is a private selected
research package, not publication, a complete backup, route-safety assessment,
or a permission grant. Export does not change a site status.

`POST /dossiers/download` takes the same selection plus the returned
`preview_hash` and `generated_at`. It rebuilds the snapshot at that timestamp;
any changed selected data or membership produces `409` and requires a new
preview. A successful response has the same JSON/HTML/GeoJSON/GPX content and
hashes as the preview.

## BK-45: selected import subset

`POST /import-subsets/preview` accepts a complete validated `source_package`,
an ordered non-empty `selected_keys` list, a fresh `new_batch_id`, and a
nonblank reason. The source package is limited to the existing 500-record
schema bound. Schema `1.0` and `1.1` are parsed with the existing package
models; the source hash uses the existing canonical hash of
`validate_import_package(source).model_dump(mode="json")`, so schema `1.0`
hashing and package contents are not redefined.

The preview response contains the exact derived package with only selected
records, the ordinary native import preview, a selection/effect hash, and a
manifest with parent batch/hash/keys, new batch ID, selected and unselected
keys, reason, derived package hash, and references from selected records to
unselected keys. Those references remain in the selected payload and are
reported unresolved; unselected records are never silently included.

`POST /import-subsets/commit` resubmits the source package, selection, exact
derived package, and both hashes. The route recomputes the subset and native
preview before calling the parent commit adapter. A selection change, source
change, derived-package change, or stale native preview rejects before the
commit callback. Independent subsets receive distinct batch IDs and each
manifest points to the same immutable source package hash. The parent adapter
is responsible for durable provenance and the existing import atomicity.

## BK-51: revision-bound GIS edit round trip

`POST /gis/edit-packs` takes 1–100 selected site IDs and returns a GeoJSON
FeatureCollection containing only site features. Each feature carries its
stable external key, internal site ID, source revision, `feature_type: site`,
and `point_role: feature`. The pack declares
`coordinate_order: longitude_latitude` and binds membership, coordinates,
roles, and revisions with `source_hash`. Approach and observation features are
not editable through this route.

`POST /gis/edit-previews` accepts the original pack, returned features, an
explicit reason, and `axis_order_confirmation: longitude_latitude`. Feature
membership/order, IDs, revisions, roles, and all properties must match the
fresh current pack; geometry is the only editable property. Geometry must be a
2D WGS84 Point in GeoJSON `[longitude, latitude]` order and pass finite/range
checks. Unchanged coordinates produce no proposed edit. Clearing a stored
coordinate, extra properties, changed roles, stale packs, and out-of-range
points fail closed.

The axis declaration is explicit and the route never auto-swaps coordinates.
When both numeric values are legal as longitude and latitude, their intended
meaning cannot be inferred from the pair alone; the preview preserves the
submitted GeoJSON order and requires the caller's explicit confirmation.

`POST /gis/edit-commits` recomputes the preview and requires its exact
`preview_hash`. A no-change commit returns `no_changes` without calling the
writer. Otherwise only changed coordinate proposals are passed to the parent
callback, each with stable key, site ID, expected revision, and old/new
`[longitude, latitude]` pairs. A missing writer returns `501`; there is no
fallback direct SQL update. The parent callback must atomically invoke the
native location/revision guard and preserve its location-review behavior.

## Integration and acceptance boundary

The module and direct-installer tests establish request validation, bounded
output, preview/hash invalidation, exact selection and allowlist behavior,
schema 1.0/1.1 subset derivation, stale GIS revision rejection, no-op behavior,
and commit delegation. The parent still needs to install these routes, provide
an atomic provenance-aware import adapter and native GIS location-guard adapter,
and run application-level integration tests against those adapters. No live
database, production route, external service, or human review was exercised by
the direct module tests.
