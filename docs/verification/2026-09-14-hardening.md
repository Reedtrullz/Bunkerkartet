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

## Review follow-up — stale GeoJSON auth and observation input

- Direct GeoJSON fetches now check the captured auth epoch immediately after `fetch`, before a delayed old 401 can clear a newer session.
- `photo_urls` now leaves non-list containers to Pydantic, producing controlled 422 responses for numeric, object, and string inputs instead of a validator `TypeError`.
- Verification: 7 synthetic browser smoke tests, 32 import API tests, full suite `94 passed, 1 warning`, Node 22 syntax, and `git diff --check` passed.
- Legacy URL read-side screening remains intentionally deferred to L05, which owns evidence/read-response preservation; no historical URL values were auto-rewritten.
