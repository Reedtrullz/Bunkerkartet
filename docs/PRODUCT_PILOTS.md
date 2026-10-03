# Product pilot helper contracts

This slice prepares bounded server and policy helpers for BK-30, BK-31, BK-32,
and BK-63. It does not enable a product pilot. Every feature is disabled by
default; `app.main` does not install these routes until the parent allocates
the migration after schema 12 and supplies an explicit policy decision.

## Integration seam

The parent can integrate the helpers with:

```python
from app.pilots import PilotPolicies, install_pilot_routes

install_pilot_routes(
    app,
    database,
    admin_guard,
    site_detail,  # callback signature: site_detail(connection, site_row)
    policies=PilotPolicies(...),
)
```

The alternative `settings=` form reads only the explicit boolean attributes
`pilot_offline_enabled`, `pilot_historical_enabled`, `pilot_readers_enabled`,
`pilot_publication_enabled`, and `pilot_geography_enabled`; missing attributes
mean disabled. Geography descriptors must also be explicitly supplied. Passing
both `settings` and `policies` is an error. The installer registers API routes,
but never applies `PILOT_SCHEMA`; the parent owns migration allocation and must
run that migration before enabling any route.

All registered writes use the injected `admin_guard`. A reader credential is
only an additional GET-guard primitive:

```python
grant = authorize_reader(connection, token, now)
grant.require_scope("sites:read")
```

The parent GET guard must check the reader pilot's explicit enabled policy
before calling it. Keep all mutation handlers behind the independent admin
guard. `ReaderGrant.require_mutation()` always denies. The helper does not
modify the app's global authorization behavior.

## BK-30: bounded offline pack and outbox

`build_offline_pack` stores only the explicitly selected sites and allowlisted
fields, binds the canonical JSON to a SHA-256 checksum, records per-site
revisions, and requires an expiry. It never includes bearer credentials, tile
data, photos, route starts, observations, or raw import payloads. Pack size,
site count, TTL, outbox count, and individual item bytes are bounded by
`OfflinePolicy`; the total stored pack count is bounded too. Expired packs stay
labeled stale until an explicit clear and continue to count against that cap.

`queue_offline_observation` uses the client's original request ID as a unique
idempotency key. A retry with identical pack/site/revision/content returns the
same outbox ID; reuse with different content fails. `assess_offline_item`
labels expired packs and marks revision changes for review. `record_offline_review`
requires a reviewer reference and current site revision. An approval only marks
the item `approved_for_sync`; this module has no sync endpoint and creates no
observation. A later parent sync controller must preserve the same request ID
through the existing observation idempotency contract and require a separate
explicit action for each item.

`clear_offline_pack(..., explicit=True)` deletes that pack and its outbox rows
through the exported cascade relationship. There is no browser persistence or
session-lock integration here. Device lock/privacy behavior therefore remains
unimplemented: the future client must distinguish session erasure from
owner-approved persisted device storage. Tile caching remains excluded pending
a separate provider-terms decision.

## BK-31: sourced historical assertions

`HistoricalAssertion` requires a source reference, typed predicate, certainty,
date context/precision, uncertainty, and curator disposition. The helper
accepts a URI or a bounded archive/reference identifier but never fetches it.
Contradictory records coexist because each has an independent assertion key;
the table has no foreign key or update path into `sites`, trust, access, generic
relations, or current approaches. `list_time_layer` retains undated assertions
as `date_match="unknown"` and marks possible/uncertain/contradicted records for
visually distinct rendering.

No owner-selected or independently reviewed historical sample was supplied.
The module provides data/list helpers only; a time-layer UI, source review, and
independent usefulness review remain gates before adoption.

## BK-32: private readers and static publication preview

`issue_reader_credential` generates a random token and returns it once. The
database stores only a random salt, SHA-256 hash, read scopes, issue/expiry
times, and revocation time. TTL and active credential count are bounded.
Stored credential receipts also have a hard cap; expired/revoked rows are not
silently discarded to make room.
`authorize_reader(connection, token, now)` rejects unknown, expired, and
revoked credentials. Reader scope is restricted to an explicit read-only
allowlist; there are no mutation scopes.

`build_publication_preview` requires a selection, rights, and coordinate
decision/reference for every candidate. Only fields on the configured
publication allowlist can enter a preview. Coordinates require both axes and
an explicit exact/generalized decision; generalized values must be supplied
by the reviewer. The preview includes included and excluded decision receipts,
binds membership and approval references to hashes, and can be persisted as a
receipt. The digest is an integrity checksum, not a cryptographic signature.
The number of retained preview receipts is bounded. The package is not hosted,
deployed, or automatically published. Rights status
is never inferred from site trust, confidence, or field history.

Canary coverage is synthetic and checks that private notes, observations, route
starts, and raw import payloads stay outside the package. No real private data
or owner-approved rights decisions were used.

## BK-63: explicit geography descriptors

`GeographyDescriptor` carries display name, collision-safe key namespace,
bounding box, map defaults, approved source namespaces, and warnings as one
descriptor. The two exported `SYNTHETIC_GEOGRAPHIES` are synthetic test
fixtures only; their centers, bounds, source labels, and basemap identifiers
are not owner-selected defaults or real operating-area claims. A production
policy must supply its own reviewed descriptor.

`geography_key` escapes each approved source namespace and external key as
separate components. `out_of_scope_review` returns the submitted WGS84
coordinates unchanged with an explicit review status. It never picks a
descriptor from GPS, moves a coordinate into a bounding box, or claims a
political/property boundary. The existing Trondheim data is untouched.

## Owner and integration gates

- Keep all policies disabled until the owner chooses device persistence,
  retention/expiry, historical sample, reader scope, publication fields/rights,
  coordinate treatment, and one actual geography descriptor.
- Parent allocates the pilot schema after v12, imports `PILOT_REQUIRED` into its
  schema verifier, and wires `install_pilot_routes` plus the reader GET guard.
- Parent sync integration must re-read each changed site and require per-item
  review; it must reuse original request IDs and the existing observation
  idempotency contract. No automatic synchronization is provided here.
- UI behavior, browser persistence/lock behavior, independent historical
  review, real publication rights, offsite/storage retention choices, and
  production acceptance remain unverified.
