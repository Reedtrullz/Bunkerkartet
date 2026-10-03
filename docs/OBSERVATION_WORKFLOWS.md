# Private observation amendments and visits

This backend slice provides four bounded contracts: append-only observation amendments (BK19), private planned/occurred visit sessions (BK53), scoped negative evidence (BK54), and storage validation for submitted measurement context (BK58). It adds no browser controls, geolocation call, background tracking, public endpoint, export, route-provider call, automatic promotion, or automatic deletion.

## Migration and application wiring

`app.observation_workflows` exports the SQL statements for migration 12 as `OBSERVATION_WORKFLOW_SCHEMA`, and required table/column inventories as `OBSERVATION_WORKFLOW_COLUMNS` and `OBSERVATION_WORKFLOW_REQUIRED_SCHEMA`. Both inventories cover `field_observations`, `observation_amendments`, `observation_visits`, and `visit_observations`. The parent database migration owns applying those statements once and checking required schema; tests and app startup rely on `database.initialize()` and must not reapply the `ALTER TABLE`. `context_json` is added to `field_observations` with `{}` for pre-existing rows; an empty legacy context means that searched scope and visibility are unknown.

After migration, `create_app` calls `install_observation_routes(app, database, admin_guard, ...)`. Every endpoint installed by this function uses the supplied admin dependency. The parent integration supplies local callbacks; question membership receives `(connection, question_id, site_id)`:

- `route_snapshot(connection, route_id)` reads a saved route and returns its immutable JSON snapshot with a `stops` list containing `site_id`. It must not call a routing provider.
- `manual_approaches(connection, site_ids)` returns exactly one current, reviewed, public approach snapshot per requested site, or `None`. The route validates site identity, paired finite coordinates, public access and review date; the callback also applies the application's full route eligibility rules.
- `question_membership(connection, question_id, site_id)` verifies that a private research question belongs to the named site. Selected visit questions and linked amendment questions fail closed without this callback.
- `observation_support(connection, site_id, observation_id)` returns true only when that observation currently supports an adopted coordinate or a selected review decision. A material amendment to such evidence sets `location_review_required` and increments the site revision in the same transaction. It never changes the coordinate, status, access, or trust level.

The native `FieldObservation` request inherits `OptionalObservationContext` to share the bounded fields and `point_role="unknown"` default. Keep `point_role` in its existing observation column. The parent native-observation handler writes optional metadata attributes to `context_json`, excluding `point_role` and native observation fields. The existing finite, paired-coordinate validator remains responsible for native observation coordinates. Instrument accuracy and declared uncertainty stay distinct from `field_observations.uncertainty_m`; no value is copied between them.

## Amendments and effective assessments

`POST /api/sites/{site_id}/observations/{observation_id}/amendments` requires a request ID, the current `expected_observation_revision`, and a nonblank reason. It accepts bounded changes to outcome, note, paired latitude/longitude, point role, uncertainty radius, observed-location text, access notes, safe photo references, optional context, or active/withdrawn status. The original `field_observations` row and its original `site_events` record are not edited. Each amendment records its request hash, reason, revision, changes, timestamp, and whether selected support was invalidated.

An exact retry returns its original receipt. Reusing the request ID with a changed payload or submitting a stale observation revision returns 409. Coordinate pairs must be complete and in range; uncertainty must be finite and bounded; photo references reject embedded credentials and non-HTTP(S) URLs. Corrupt original context, photos, coordinates, or amendment history fail closed.

`GET /api/sites/{site_id}/observations/{observation_id}/effective` returns `original_snapshot`, current effective assessment fields, merged optional context, `observation_revision`, `status`, `withdrawn`, `support_invalidated`, and an amendment history. The parent promotion/adoption integration consumes this view under the stricter evidence contract: accept/research requires a reason and `evidence_ids`; field verification/confirmation requires `observation_ids`; confirmation is blocked by unresolved questions and `location_review_required`. Withdrawn or negative assessments cannot silently serve as supporting evidence. A negative observation remains evidence about one dated assessment; repeated negative outcomes never reject or destroy a site automatically.

For `not_found` or `inaccessible`, optional context can record `sought_target`, `public_viewpoint_description`, `visibility_limits`, `coverage_unknown`, and a linked research question. These are curator-entered limits, not a searched polygon, permission to enter, or proof of absence. If a legacy assessment has no such details, the effective response labels `negative_context_status` as `unknown` and does not infer them.

## Private visits

`POST /api/visits` creates a revision-1 `planned` session with a planned date and exactly one source: a saved route snapshot or explicit manual approach IDs. The route stores the callback's route snapshot or validated manual approach snapshot and selected `{site_id, question_id}` memberships. A visit request ID makes creation retries idempotent. Optional `retention_until` is private metadata; no deletion or purge runs automatically, and an omitted value is returned as `unspecified` for owner policy review.

`PATCH /api/visits/{id}` requires `expected_revision`. A `planned` visit can move to `occurred` only with a separate non-future occurred date, an explicit visit outcome, and exactly one `answered`, `skipped`, or `not_reached` outcome for every selected question. An `occurred` visit can move to `closed`; closed records cannot receive more observations. Each transition increments the visit revision. These outcomes never update research-question state, so a skipped question stays open.

`POST /api/visits/{id}/observations` attaches an existing observation ID only after the visit occurred and only when its site belongs to the route/manual snapshot. A supplied question must be selected for that same site, still belong to it, and have an `answered` visit outcome. Attachments use a visit-scoped request ID and a unique visit/observation pair; retries return the existing receipt. Observation rows are never rewritten. Every visit read and write is protected by the admin dependency, and visit records are not part of ordinary catalogue endpoints or exports.

## Backend boundary

This module records device measurements only when a caller explicitly submits them. It does not access browser location APIs, request permission, poll a device, or save a draft on its own. The frontend owner remains responsible for one-shot permission/freshness/cancellation behavior and for showing original versus effective assessment labels. This slice does not claim owner, device, field, or production acceptance.
