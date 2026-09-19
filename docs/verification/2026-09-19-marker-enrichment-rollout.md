# Marker enrichment rollout verification — 2026-09-19

## Scope

- Worktree: `/Users/reidar/.codex/worktrees/bunkerkartet-leira-enrichment/Bunkerkartet`
- Branch: `codex/leira-battery-enrichment`
- Verification head before this report commit: `2fbee103`
- Base: `b56e7b347fe72cfbcc559d9861730f506e68264d`
- Runtime overlay: additive only; no database or deployment files changed.

## Research and overlay coverage

- Local research report: 85 external-key rows, verified with the awk row-count check.
- Overlay: 20 site keys total — the existing 11 records plus the nine first-wave keys `krigskart:2663`, `krigskart:2673`, `krigskart:2676`, `krigskart:2677`, `krigskart:3475`, `krigskart:721`, `krigskart:742`, `krigskart:3479`, and `kystfort:topic:414`.
- All overlay records carry an explicit `research_state: curated`.
- Every first-wave claim references a source in its own record; every first-wave source URL is absolute HTTPS.
- Identity queue additions are `CQ-011`–`CQ-015`; existing `CQ-001`–`CQ-010` were preserved.

## Verification commands

All Python checks used the existing project environment at
`/Users/reidar/Projectos/Bunkerkartet/.venv/bin/python`; system Python did not
have the repository dependencies installed.

- Full suite: `168 passed, 1 warning in 41.03s`.
- Focused enrichment/API/frontend suite: `18 passed, 1 warning`.
- Focused content/API suite after the first wave: `17 passed, 1 warning`.
- Browser smoke: focused enrichment/detail/fallback checks passed; the full suite included the complete browser test module.
- JavaScript syntax: `node --check app/static/app.js` passed.
- Diff whitespace: `git diff --check` passed.

## Preview evidence

Read-only `TestClient` previews for Leira batteri, Polsmohulen, and
Østmarkneset showed `candidate` status and `unknown` canonical access while
returning `research_state: curated`, review date, source-backed claims, and
conservative access text. A synthetic site without an overlay returned
`enrichment: null`; the browser fallback renders `Ikke kuratert i kartet` and
does not render the old `Ikke beriket i kildeunderlaget.` text.

## Live read-only checks

- `GET https://bunker.reidar.tech/api/health` → `200`, healthy, live version `b56e7b347fe72cfbcc559d9861730f506e68264d`.
- Unauthenticated `GET https://bunker.reidar.tech/api/sites` → `401`.
- No live import, commit, edit, merge, or deploy endpoint was called.

## Non-claims

- No field verification, ownership confirmation, access permission, coordinate correction, status promotion, or duplicate merge was performed.
- The research report remains local curator material and is intentionally not a public runtime import.
- The branch is ready for owner review only; no push, PR, production import, merge, or deploy was performed.

## Final review

- The requested read-only reviewer was dispatched but shut down after bounded timeouts without returning a review; no reviewer findings were therefore treated as evidence.
- Native self-review found no unresolved Critical or Important implementation issue. It added one regression test proving that a legacy overlay record without `research_state` still defaults to `curated`.
- Final full suite after that test: `168 passed, 1 warning`.
