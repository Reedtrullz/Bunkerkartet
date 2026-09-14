# Bunkerkartet hardening verification — 2026-09-14

## Baseline / L00

- Worktree: `/Users/reidar/.codex/worktrees/749b/Bunkerkartet`
- Branch: `codex/audit-hardening`
- Baseline SHA: `f2a903b371ee3738fb748bb4b289eb71009cf222`
- `origin/main`: same SHA after `git fetch origin main`; no newer upstream diff.
- Runtime used: Python 3.12.13, Node 22.22.3.
- Disk: 65 GiB available at start of work.
- Initial environment deviation: no worktree virtualenv; `/Users/reidar/.local/bin/node` is malformed. A local `.venv` and explicit NVM Node 22 were used. The malformed wrapper was not changed.
- Baseline before changes: 70 existing pytest tests passed; one existing Starlette/AnyIO deprecation warning.

## Leveranse I — L01–L02

Initial commit: `7ac17f6`. Review follow-up: recorded in Git after fresh verification.

### Changes

- GPX Blob URLs are retained on a normal keyboard-focusable download link until the route result is replaced or the private workspace is cleared, so repeated downloads remain possible.
- Added a small Playwright server with synthetic data and a stubbed ORS response. No production token, GPS position, ORS call, or production database is used.
- Added browser regression for delayed saved-route GPX download.
- Added `authEpoch` stale-response protection for private API calls.
- Added a visible Lock control and one cleanup path for private DOM, lists, route state, import state, markers, start marker, and object URLs.
- Auth failures and stale async responses no longer repopulate cleared private DOM.
- 401 responses from detail/private API calls share the same cleanup path as map loading; cleanup re-enables load and resets import/route busy controls.
- GeoJSON body reads, delayed file reads, and import/commit/route finalizers are guarded after their last await and by auth epoch/import generation.
- Added `playwright==1.55.0` as a dev-only dependency.

### Verification

```text
.venv/bin/python -m pytest -q tests/test_browser_smoke.py
6 passed (including repeated/tastatur GPX, 401 detail cleanup, retry/busy reset, delayed response and delayed file read)

.venv/bin/python -m pytest -q
84 passed, 1 warning in 13.80s

Node v22.22.3: node --check app/static/app.js -> 0
git diff --check -> 0
```

### Non-claims

- This proves the local browser lifecycle and synthetic auth cleanup only. It does not prove live ORS availability, route walkability, legal access, field verification, or deployment.
- The source catalogue, production database, GitHub settings, and deployment were not changed.

## L03 — Reference URL boundary

Status: implemented locally; commit follows fresh verification.

- `validate_reference_url` rejects URL userinfo and conservative credential-like query names, including encoded nested URLs, without returning the raw value.
- The same validator is used for source URLs and observation photo URLs.
- FastAPI and the research CLI serialize only `loc`, `type`, and controlled `msg`; Pydantic `input`/`ctx` are not returned.
- Normal map/object query parameters remain intact.

Verification: targeted URL/API/CLI tests passed (`8 passed` across the new L03 cases); the full suite and Node 22 syntax check were run with the L01–L02 follow-up and passed (`84 passed, 1 warning`; `node --check` exit 0). No production source was edited or sanitized.

Non-claims: this does not determine whether any legacy URL parameter is an actual private key. Source/catalogue 139 still requires a separate owner decision and private remediation review.

## L04 — Schema and restore boundary

Status: implemented locally; no production database or backup was opened.

- New databases receive numbered schema version 1 and repeated initialization is idempotent.
- Existing databases are inspected before mutation. Incomplete schemas, future versions, and non-SQLite bytes fail closed; an existing legacy v0 schema is migrated additively with the confidence backfill and version marker in one transaction.
- `scripts/verify_database.py` opens SQLite with `mode=ro`, checks integrity, foreign keys, expected version, required tables/columns, and prints only version and row counts.
- Deployment guidance now requires a separate staging area, consistent SQLite/WAL/SHM handling, archive member rejection for traversal/symlink/hardlink/unexpected files, read-only verification before replacement, and a retained rollback archive.

Verification:

```text
.venv/bin/python -m pytest -q
90 passed, 1 warning in 14.95s

synthetic archive -> staged extraction -> scripts/verify_database.py
version=1 integrity_check=ok foreign_key_check=empty sites=0 ... route_plans=0
synthetic_restore=passed

git diff --check -> 0
```

Non-claims: the restore exercise used a newly generated empty synthetic database. It does not prove production backup completeness, recovery-point coverage, live-volume behavior, or deploy success. The verifier is intentionally read-only; it does not repair or upgrade a database.

L04 review remediation:

- Schema migrations now run behind explicit `BEGIN IMMEDIATE`/commit/rollback; an injected failure after the first v2 `ALTER TABLE` leaves no added column/table and a clean retry succeeds.
- Restore preflight accepts the harmless `./` root entry produced by the documented `tar -C /data .` backup, rejects duplicate/traversal/link/unexpected members, and stages outside the target volume before verification and replacement.
- Synthetic archive coverage uses a non-empty database made with the documented tar shape plus malicious traversal, symlink, and duplicate-member archives; `10 passed` in the focused DB/archive tests.

## L05 — Evidence preservation and batch identity

Status: implemented locally; no historical production values were rewritten.

- Schema v2 adds immutable `evidence_items` and `import_batches.payload_hash`; v1 evidence is reconstructed from matching `import_records.payload_json` when possible and otherwise preserved as `legacy_unresolved`. v2 migrations are retryable.
- Same-URL imports retain each site-specific excerpt/provenance item; source identity metadata fills only missing fields rather than overwriting known values.
- Preview exposes a deterministic SHA-256 payload hash. Commits use `BEGIN IMMEDIATE`; an identical committed retry is idempotent, while a changed payload with the same batch ID returns 409.
- Legacy credential-bearing source URLs and observation photo URLs are withheld in read responses with a safe status; raw values are not returned or made clickable. Historical values are not auto-sanitized.
- Migrated evidence dates use the evidence item's nullable values directly; source-level dates are used only when no evidence item exists. Merge uses `ON DELETE SET NULL` for unresolved legacy links so historical evidence is not deleted.

Verification: focused L05/API/DB tests `46 passed, 1 warning`; full suite including 7 browser smoke tests `102 passed, 1 warning`; Node 22 syntax and `git diff --check` passed.

Non-claims: this does not approve or classify any legacy source, prove source ownership, or validate field observations. L06 preview freshness and later schema versions remain outstanding.

## L06 — Fresh, deterministic import preview

Status: implemented locally; no import was committed to production.

- Preview now returns deterministic `payload_hash` and `preview_hash`, sorted effect records, before/after changes, preserved fields, evidence excerpts, and warnings without generated database IDs.
- New commits require `X-Import-Preview`; the server recomputes the preview under the same `BEGIN IMMEDIATE` transaction. Identical committed retries remain idempotent; missing or stale previews return 409 without writes.
- The browser renders a text-based review with technical JSON as a secondary disclosure, captures import generation and preview hash, rejects stale preview responses after file changes, and disables file changes during commit.
- Documentation and research examples now pass the preview hash explicitly.

Verification: full suite `111 passed, 1 warning`; Node 22 syntax and `git diff --check` passed. The browser preview race is covered with synthetic delayed Chromium responses.

Non-claims: preview hash is a consistency check, not proof of human review, approval, source permission, field validation, or production import.

## L07 — Location invariants and observation roles

Status: implemented locally; no production database or catalogue values were changed.

- Import records and effective site edits share one location validator: coordinate pairs must be complete and finite, coordinate-bearing locations require a finite radius, approximate locations require a positive radius, and exact locations cannot use `llm_inference`.
- Field observations now record `point_role` (`feature`, `entrance`, `viewpoint`, or `unknown`) and nullable `uncertainty_m`; only a found `feature` observation with an explicit radius can replace a site coordinate.
- Adoption records an approximate, explicit-coordinate site with the observation radius and keeps the existing audit event.
- Schema v3 adds the observation fields with a default `unknown` role and nullable radius; migration remains transactional and retryable.

Verification: full suite `115 passed, 1 warning`; Node 22 syntax and `git diff --check` passed. Disk guard showed 63 GiB available before the final test run.

Non-claims: this validates application and migration behavior only; it does not establish coordinate truth, field verification, legal access, or production deployment.

## Review remediation after L06

- v1→v2 evidence migration now reconstructs every unique historical `(import_record_id, source_index)` for the linked site/source, including same-site same-URL imports with different excerpts and dates; unmatched or uncertain mappings remain `legacy_unresolved`.
- Import-file changes clear `pendingImport`, preview hash, displayed result, and preview/commit controls synchronously. A delayed current file read is the only path that re-enables Preview.
- Commit computes duplicate warnings once before writes using the same batch-peer set as Preview, so persisted peer warnings do not depend on input order or newly inserted peer rows.

Verification: full suite `118 passed, 1 warning`; browser smoke `9 passed`; Node 22 syntax and `git diff --check` passed. No production import, database, push, PR, merge, deploy, or catalog mutation was performed.

## L08 — Reviewed public approaches and stable route stops

Status: implemented locally; no live route or production database was used.

- Schema v4 adds explicit site approach coordinates, `approach_access`, review note/timestamp, and nullable route `stops_json`; v5 adds the review/warning columns, while v6 is the forward repair for databases that had already recorded v5 before the final evidence repair code existed.
- New routes accept only `site_ids`; the server resolves current reviewed public approach points and rejects missing, merged, unreviewed, or `location_review_required` sites before ORS. The structure coordinate and site access label do not grant route eligibility.
- Saved routes retain stable stop IDs, names, role, reviewed timestamp, access note, stop warnings, provider warnings, and a `current_site_changed` signal. Legacy waypoint routes remain readable and are explicitly marked as uncontrolled legacy routes.
- Provider `way_points` indices are validated when present; missing indices produce an explicit uncontrolled-snapping warning. Named stop deviations over 50 m are retained in route warnings and GPX labels.
- The UI exposes approach review controls and observation point-role/radius controls; adoption remains disabled unless the observation is a feature with an explicit radius.

Verification: full suite `130 passed, 1 warning`; focused route/DB tests and 13 browser-smoke tests passed; Node 22 syntax and `git diff --check` passed. Synthetic local v4 and v5 databases were upgraded by forward repair migrations; no production database was opened.

Non-claims: reviewed public approach is not structure access, ownership, safety, field verification, or proof that an ORS route is walkable. Actual catalogue approach decisions remain curator-owned.

## L09 — Opt-in route-start location

Status: implemented locally; no real user location was requested.

- Geolocation remains one-shot and opt-in via `getCurrentPosition`; it uses a 10-second timeout and does not use `watchPosition` or background tracking.
- The route UI reports the browser-provided accuracy as start-point information only. It never converts that accuracy into a site radius or changes a site record.
- Permission failure leaves a manually chosen route start intact. The default is labelled `Standardstart i Trondheim`, and the route panel explains that route start/stops are sent to ORS and stored in private route history when a route is calculated.

Verification: synthetic Chromium geolocation/browser smoke passed as part of the full suite (`130 passed, 1 warning`); delayed GPS success after Lock is ignored by the auth epoch guard. Node 22 syntax and `git diff --check` passed.

Non-claims: this does not establish a personal-data retention policy for shared use, prove a real GPS fix, or prove route walkability.

## L10 — Norwegian responsive operator surfaces

Status: implemented locally; no production UI was changed.

- The operator UI now declares Norwegian language metadata, uses Norwegian labels/statuses for map, review, import, and route actions, and exposes three ordinary surface buttons: `Kart`, `Vurdering`, and `Tur`.
- The map legend is collapsed by default, the primary accent meets the light-theme contrast target, the layout prevents horizontal overflow on narrow screens, and reduced-motion preferences disable smooth scrolling transitions.
- Geolocation success and error callbacks capture the initiating `authEpoch`; Lock clears the location status and invalidates delayed callbacks before they can restore the private route start.

Verification: full suite `130 passed, 1 warning`; Node 22.22.3 syntax check passed. Browser coverage includes a delayed synthetic GPS callback after Lock. No production UI or data was changed.

Non-claims: this is not a visual acceptance sign-off for every device/browser combination; live catalog content and production deployment remain out of scope.

## Review follow-up — v6 forward repair from recorded v5

Status: implemented locally; no production database was opened.

- `CURRENT_SCHEMA_VERSION` was 6 at this review point. Fresh databases ran v6, and existing v5 databases ran an idempotent forward repair that rechecked legacy evidence foreign keys and historical evidence reconstruction.
- A dedicated test starts from a database explicitly marked v5, then proves it reaches v6 and repairs an unresolved legacy evidence row. This avoids claiming that only a v1 fixture covers the migration path.

Verification: `tests/test_db.py` passed with 14 tests; the full suite passed with `130 passed, 1 warning`. This was a review follow-up, not canonical L11. No production migration, backup, or catalog mutation was performed.

## Review follow-up — stale GeoJSON auth and observation input

- Direct GeoJSON fetches now check the captured auth epoch immediately after `fetch`, before a delayed old 401 can clear a newer session.
- `photo_urls` now leaves non-list containers to Pydantic, producing controlled 422 responses for numeric, object, and string inputs instead of a validator `TypeError`.
- Verification: 7 synthetic browser smoke tests, 32 import API tests, full suite `94 passed, 1 warning`, Node 22 syntax, and `git diff --check` passed.
- Legacy URL read-side screening remains intentionally deferred to L05, which owns evidence/read-response preservation; no historical URL values were auto-rewritten.

## L11 — Synlige relasjoner og bedre duplikatvurdering

Status: implementert lokalt; ingen katalogverdier ble automatisk slått sammen.

- Schema v7 adds `site_relations` and idempotently migrates historical
  `related_site_keys`, including unresolved external keys shown as `ikke
  importert`. New self-relations are rejected.
- Site detail exposes related target key/name/status. Merge targets include
  valid surviving reviewed sites, while evidence, observations, relations,
  and event references remain traceable. No automatic merge or statusheving is
  performed.
- Duplicate warnings normalize name whitespace/case and classify overlapping
  uncertainty as `mulig relasjon`, not proof of identity.

## L12 — Observasjonsretry, redigeringskonflikt og lesbar historikk

Status: implementert lokalt; schema is v8 and no production database was
opened.

- `sites.revision` is returned by site reads; PATCH requires
  `expected_revision`, and all import/review/merge/coordinate/approach and new
  observation mutations advance it. Idempotent observation retries do not.
- Field observations carry nullable `request_id`/`payload_hash` identity with
  a unique `(site_id, request_id)` index. `BEGIN IMMEDIATE`, rowcount checks,
  and explicit merge collision rejection cover concurrent retries and merges.
- Auth-protected event history is bounded at 100 entries, with explicit safe
  field projections and edit before/after payloads. Location review requires a
  nonblank reason and current revision.

Verification: focused concurrency/revision and worker-pool tests passed; the
full local suite was `136 passed, 1 warning` excluding browser smoke, with `12
passed` browser smoke.

## L13 — Ressursgrenser, readiness og CI-kontroller

Status: implementert lokalt; no GitHub settings or deployment state was
changed.

- Import preview/commit stream and reject bodies over 2 MiB before JSON
  validation; models cap packages at 500 records, 20 sources per record, and
  30 warnings of 1,000 characters.
- ORS uses a per-process `BoundedSemaphore(2)`, no wait queue, and returns
  `429` with `Retry-After` when occupied. `/api/health` remains compatible;
  `/api/ready` checks integrity/schema/auth and reports optional ORS separately.
- CI retains full SHA-pinned actions, Python 3.12, read-only/non-root runtime
  hardening, and now installs only Chromium for the browser smoke job.

## L14 — Kuratorkø, policy og overlevering

Status: implementert som privat policy-/releaseunderlag; no private site
decisions, coordinates, production data, or GitHub policy changes were added.

- `docs/CURATION_POLICY.md` defines private-by-default review fields, the
  named unresolved queue, owner decisions, and the non-automatic meaning of
  `related`.
- The research prompt preserves schema 1.0 and its hash contract, separates
  source excerpt from rationale, and defers any future evidence metadata to an
  explicit versioned contract.
- `docs/DEPLOYMENT.md` records readiness/limit behavior and recommends
  required CI, blocked force-push/deletion, and auditable owner bypass without
  changing GitHub settings.

## Final local matrix

| Area | Evidence | Boundary |
|---|---|---|
| Auth, import, DB, routes | 136 non-browser tests passed | local synthetic data only |
| Browser/UI | 12 Chromium smoke tests passed; Node 22.22.3 syntax check passed | not visual acceptance for every device |
| Synthetic user flow | package → preview/review → observation → reviewed approach → saved/reopened route → GPX is covered by API/browser fixtures | does not prove real source truth, ORS-live, access, safety, or field verification |
| Schema | fresh and forward migrations reach v8; migration tests pass | no production migration |
| Drift/limits | body, cardinality, readiness, ORS busy, SHA/digest checks pass | no load test or live ORS quota use |
| L10 follow-up | mobile 390×844 keyboard surface navigation and `scrollWidth` guard pass; marker/detail labels remain named | broad device visual acceptance, field-error copy, and dense-marker/clustering behavior remain bounded local checks |
| Curation | policy and queue structure documented | owner must decide identity/access/public selection |
| Release | runbook and release policy documented | no publish, deploy, merge, push, or GitHub-setting mutation |
