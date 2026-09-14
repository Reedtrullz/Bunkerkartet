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

Commit: recorded in Git after verification.

### Changes

- GPX Blob URLs are created on the user click and retained briefly after the browser starts the download; they are revoked when replaced or the private workspace is cleared.
- Added a small Playwright server with synthetic data and a stubbed ORS response. No production token, GPS position, ORS call, or production database is used.
- Added browser regression for delayed saved-route GPX download.
- Added `authEpoch` stale-response protection for private API calls.
- Added a visible Lock control and one cleanup path for private DOM, lists, route state, import state, markers, start marker, and object URLs.
- Auth failures and stale async responses no longer repopulate cleared private DOM.
- Added `playwright==1.55.0` as a dev-only dependency.

### Verification

```text
.venv/bin/python -m pytest -q tests/test_browser_smoke.py
3 passed in 8.69s

.venv/bin/python -m pytest -q
73 passed, 1 warning in 7.72s

Node v22.22.3: node --check app/static/app.js -> 0
git diff --check -> 0
```

### Non-claims

- This proves the local browser lifecycle and synthetic auth cleanup only. It does not prove live ORS availability, route walkability, legal access, field verification, or deployment.
- The source catalogue, production database, GitHub settings, and deployment were not changed.
