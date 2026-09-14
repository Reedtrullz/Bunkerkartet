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
