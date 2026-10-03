# Bunkerkartet All Open Issues and PRs Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for native implementation, or superpowers:subagent-driven-development when delegation is justified and policy-approved. Complete the checklist for each item before proceeding past its acceptance gate.

**Goal:** Resolve all 65 open issues and all five open PRs through focused, dependency-ordered changes and explicit evidence-backed decisions.

**Architecture:** Extend the maintained FastAPI/SQLite/Leaflet/vanilla-JS application. Reuse its transaction, revision, candidate-import, provenance and reviewed-approach safeguards. Selectively port the existing editor, keep one schema migration sequence, and introduce optional advanced features as bounded private workflows.

**Tech Stack:** Python 3.12 production/CI lane, FastAPI, Pydantic, SQLite/WAL, Leaflet, vanilla JavaScript, pytest/Playwright, immutable Docker images, GitHub Actions, Ansible/Compose.

**Spec:** [Frozen issue/PR requirements and evidence](2026-10-03-bunkerkartet-all-open-items-spec.json). This snapshot preserves every issue body, declared dependency and original acceptance target. Existing design context: ../../proposals/2026-09-30-improvement-portfolio.md and ../../proposals/2026-09-30-improvement-portfolio-round-2.md. Current GitHub issue text is authoritative if intentionally changed after this snapshot.

**Date / scope:** 3 October 2026, Europe/Oslo. “Every issue and PR” means every currently open item; the seven historical closed/merged PRs are reconciled below. This document is a plan, not implementation, GitHub mutation, deployment or private-data approval.

## Global Constraints

- Preserve the dirty primary checkout, modified README, untracked plans/research/browser artifacts, and SQLite database/WAL/SHM. Work from an isolated maintained-source worktree at execution time.
- Baseline source is remote main `1e72fd3feaf7d64ef3d2d845101852bebe89047d`; checked-out historical main is `26600b9a83156ba3dd74a2487db1ce9dcfd215e6`. Do not reset/switch the primary checkout.
- Python 3.12 remains the approved production and CI lane unless the owner explicitly changes the runtime policy. Node CI remains 22. Keep immutable Action/image references.
- Imports remain candidate-only. Sparse candidate imports cannot overwrite reviewed fields. Exclude snublesteiner and preserve original schema-1.0 payload/hash/retry identity.
- Keep identity, source support, lifecycle, physical location, reviewed public approach, structure access, freshness, and publication permission as separate facts.
- Preserve private bearer protection, revision checks, provenance/history, safe validation projection, private response no-store behavior and recovery backups.
- Never guess radius, permission, field observation, source independence, ownership, historical certainty or metadata for unknown legacy data.
- Record source-backed references and short quotations; do not copy full articles or unlicensed images into the repository.
- No new ORM, account platform, scraper, app-owned LLM, multi-tenant infrastructure, generic event-sourcing rewrite or speculative caching/indexing.
- Check `df -h /System/Volumes/Data` before sustained builds/tests; stop below 30 GiB. Use bounded run-local scratch and preserve active/unowned artifacts.
- Production deploy/restore/rotation, branch-rule changes, backup transfers/deletions, publication and field-trust decisions each need their applicable owner authorization. Prepare reviewable local results first.
- Prefer native implementation initially. Any delegation must obey the current AGENTS.md route/capability/freshness/account-cap policy; this plan does not authorize a stronger route or fan-out.

## Review Focus

1. Lost responses after a committed import/observation/route: preserve original request identity and resolve via readback. Tests belong to BK-04/07/20/44/45.
2. Concurrent edits or stale merge target: reject missing/stale caller revisions without rows/events changing. Tests belong to BK-02/14/46/60.
3. Legacy and corrupt state during migration/restore: preserve healthy/history data, distinguish absent/empty/corrupt, fail before replacement and retain recovery. Tests belong to BK-25/33/37/38/39/59.
4. Lock plus delayed network/file/GPS callbacks: erase private session state and prevent repopulation; no tile egress in disabled mode. Tests belong to BK-06/20/42/43/58.
5. Research/field/export confusion: candidate public label is not route eligibility, negative result is not absence, rights unknown is not permission, and GPX validity is not access or safety. Tests belong to BK-08/17/24/32/50/54/57.

## Verified inventory and source selection

- GitHub API lists **65 open issues**, **zero closed issues**, and **five open PRs**. BK-01–BK-65 map exactly to issues #13–#77. Issue comments are empty; all five open PRs have no review or discussion comments in this snapshot.
- Current main is the functional 54-file application, schema v8. Latest inspected main CI run [36479124407](https://github.com/Reedtrullz/Bunkerkartet/actions/runs/36479124407) succeeded at that exact SHA; this is historical CI evidence, not a new test run or production check.
- GitHub main branch reports `protected=false`. No policy was changed.
- The local editor ref `codex/site-content-editor` at `0890bddef1ffa182c6d0caff90d6396cdc3a934c` already validates duplicate source/claim IDs and has a v9 content override migration. Its model omits main's newer research_state; its tree also changes older seed content and policy. **Port selected functionality; do not merge its entire tree.**
- Reading `app/main.py::create_app`, `Database.connect/initialize`, import projection, route normalization, `_source_rows`, enrichment validators, browser fixtures, CI and deploy code confirmed the implementation boundaries used below.
- Synthetic/local test results, historical CI, newly green PR CI, built-image readiness, live owner flow, source/rights acceptance and field/device acceptance must have separate receipts.
- No application was started, dependencies installed, database/private records read, provider called, or live deployment checked in this planning session.

## Delivery sequence

Run phase 0 first. Within each wave, the listed order is dependency-safe. A wave is a planning group, not one giant PR. Prefer one focused PR per issue; where two issues share one indivisible contract, retain separate acceptance checklists and a documented closure mapping. Optional language in issue prose does not create a hidden hard dependency.

| Wave | Deliverable | Ordered BK tasks | Exit gate |
|---|---|---|---|
| 1 — Core correctness and safe tooling | Protect state and inputs; establish safe private session/map behavior. | [01](#bk-01-issue-13), [02](#bk-02-issue-14), [03](#bk-03-issue-15), [04](#bk-04-issue-16), [06](#bk-06-issue-18), [08](#bk-08-issue-20), [10](#bk-10-issue-22), [34](#bk-34-issue-46), [35](#bk-35-issue-47), [36](#bk-36-issue-48), [40](#bk-40-issue-52), [42](#bk-42-issue-54), [21](#bk-21-issue-33) | Every selected task's tests and acceptance receipt; owner gates remain visible. |
| 2 — Persistence, editor and route foundations | Qualify storage and recovery; integrate editor and correct route/render state. | [33](#bk-33-issue-45), [37](#bk-37-issue-49), [38](#bk-38-issue-50), [39](#bk-39-issue-51), [25](#bk-25-issue-37), [07](#bk-07-issue-19), [09](#bk-09-issue-21), [11](#bk-11-issue-23), [12](#bk-12-issue-24), [18](#bk-18-issue-30), [50](#bk-50-issue-62), [20](#bk-20-issue-32), [41](#bk-41-issue-53) | Every selected task's tests and acceptance receipt; owner gates remain visible. |
| 3 — Research workflows and operational controls | Make research usable and release/backup/auth policy reviewable. | [05](#bk-05-issue-17), [13](#bk-13-issue-25), [15](#bk-15-issue-27), [16](#bk-16-issue-28), [44](#bk-44-issue-56), [49](#bk-49-issue-61), [52](#bk-52-issue-64), [43](#bk-43-issue-55), [22](#bk-22-issue-34), [23](#bk-23-issue-35), [26](#bk-26-issue-38), [27](#bk-27-issue-39), [28](#bk-28-issue-40) | Every selected task's tests and acceptance receipt; owner gates remain visible. |
| 4 — Consequential review and content history | Preserve consequential decision support and revision/content lineage. | [14](#bk-14-issue-26), [17](#bk-17-issue-29), [19](#bk-19-issue-31), [46](#bk-46-issue-58), [60](#bk-60-issue-72), [24](#bk-24-issue-36) | Every selected task's tests and acceptance receipt; owner gates remain visible. |
| 5 — Bounded advanced workflows and verification | Deliver bounded field/GIS/research workflows and integration invariants. | [29](#bk-29-issue-41), [45](#bk-45-issue-57), [47](#bk-47-issue-59), [48](#bk-48-issue-60), [51](#bk-51-issue-63), [53](#bk-53-issue-65), [54](#bk-54-issue-66), [55](#bk-55-issue-67), [56](#bk-56-issue-68), [57](#bk-57-issue-69), [58](#bk-58-issue-70), [59](#bk-59-issue-71), [61](#bk-61-issue-73), [62](#bk-62-issue-74) | Every selected task's tests and acceptance receipt; owner gates remain visible. |
| 6 — Explicitly gated product pilots | Complete owner-selected pilots with separate privacy/rights/device acceptance. | [30](#bk-30-issue-42), [31](#bk-31-issue-43), [32](#bk-32-issue-44), [63](#bk-63-issue-75), [64](#bk-64-issue-76), [65](#bk-65-issue-77) | Every selected task's tests and acceptance receipt; owner gates remain visible. |

The principal coupled path is revisions → editor → questions → merge/promotion → amendments → visits. The persistence path is connection ownership → schema qualification → interruption-safe migrations → sequence verification. Route draft identity precedes endpoint/provenance/time-budget features. Do not schedule downstream work merely because an earlier PR is open.

**Parallelism:** Serial ownership of app/db.py migrations and coupled app/main.py/app.js changes. Independent docs, fixture preparation and read-only review can overlap. After each merge, rebase the next item and verify the new integration state; do not invent many incompatible schema-v9 branches. Tests using new schema fixtures must be qualified through the shared migration suite.

**Sizing:** Core guard/docs/validator changes are small; editor/recovery/route contracts and new persistent workflows are medium to large. Offline/publication/raster/photo pilots are large and conditional. Establish elapsed-time estimates only after phase 0 baseline and the first measured wave; there is no defensible calendar promise yet.

## Phase 0: execution preflight and existing PRs

- [ ] Re-enumerate issues/PRs/current head SHAs and compare with this frozen inventory. Account explicitly for additions, closures, superseding PRs and changed requirements.
- [ ] Inspect existing attached worktrees and choose/create a clean isolated worktree from fresh remote main; record branch/base and preserve primary WIP. Use codex/ branch prefix.
- [ ] Check disk headroom, declared Python 3.12 runtime and resolved dependencies; capture a redacted dependency receipt. Run baseline checks below using a synthetic data directory.
- [ ] Read prior failing logs and reproduce the browser transition in isolation with artifacts before assigning a cause to a dependency.
- [ ] Triage existing PRs in recommended order: #7, #9, #11; then #12 after browser diagnosis/repair; disposition #1 under the runtime policy.
- [ ] Keep original dependency PRs where viable; push/rebase/retrigger/comment/merge only in a subsequent authorized execution task.

### Existing PR resolution table

| PR | Snapshot evidence | Resolution plan | Completion evidence |
|---|---|---|---|
| [#7](https://github.com/Reedtrullz/Bunkerkartet/pull/7) — docker/build-push-action 7.3.0 → 7.4.0 | Head 71d94a1abaa8a062c7f9349e7e30ab0c3cd304ed; MERGEABLE/CLEAN; test SUCCESS, publish SKIPPED in run 35651013424. One pinned Action SHA changes. | Confirm upstream Action commit/release mapping and permissions; refresh on current main and validate build metadata/digest path. PR CI does not exercise publish, so prepare publish validation separately. | Current-head test/build green; pinned Action integrity checked; after authorized merge, main publish emits exact SHA-tag/digest receipt. No deploy inferred. |
| [#9](https://github.com/Reedtrullz/Bunkerkartet/pull/9) — Playwright 1.55.0 → 1.63.0 | Head 182b4c01ffc952a870ed5c93314d657da35ffb1f; MERGEABLE/CLEAN; test SUCCESS, publish SKIPPED in run 35651020978. | Review upstream release compatibility; install matching Chromium revision in isolated Python 3.12 environment; run full browser flow and BK-11 isolation checks. Use this single pinned browser lane for httpx2 A/B comparison. | Current-head browser/unit/build checks green, exact browser runtime receipt; selected Firefox/WebKit coverage when BK-22 lands. |
| [#11](https://github.com/Reedtrullz/Bunkerkartet/pull/11) — uvicorn 0.52.4 → 0.54.0 | Head 2d2ac2bd967ad9c9d047629c78876d01cf0a099f; MERGEABLE/CLEAN; test SUCCESS, publish SKIPPED in run 36479228810. | Review upstream changes, preserve app.main:app invocation and explicitly test start/stop/lifespan, bounded request handling, non-root container readiness and failure startup. Reverify after BK-39 startup refactor. | Current-head tests/build/startup smoke green on Python 3.12; no live deployment assertion. |
| [#12](https://github.com/Reedtrullz/Bunkerkartet/pull/12) — httpx2 2.12.0 → 2.13.1 | Head 82079e193a34f25fd78a7a5d91a53097c4afd7e6; MERGEABLE/UNSTABLE; test FAILURE in [36479238167](https://github.com/Reedtrullz/Bunkerkartet/actions/runs/36479238167). Browser: 1 failed, 21 passed; complete operator flow waited 30s for visible Legg til rute after approach save. | Capture response status, active surface, DOM visibility and server trace. Compare old/new httpx2 with identical source, Playwright, Python and fresh per-test DB. Repair proven application/test-state issue through BK-11/BK-06 as needed. If a package incompatibility is reproduced, make minimal compatible fix or hold this upgrade with explicit evidence. | Root-cause or bounded non-reproduction receipt, meaningful regression and current-head green CI; rerun-only success and longer timeout are insufficient. |
| [#1](https://github.com/Reedtrullz/Bunkerkartet/pull/1) — Python image 3.12 → 3.14 | Head f3fb6e05ba35d9fd3409d9d6f235073548020343; mergeability UNKNOWN; test FAILURE in [34798019338](https://github.com/Reedtrullz/Bunkerkartet/actions/runs/34798019338): runtime-policy assertion expects python:3.12-slim; 1 failed, 67 passed. The failing suite ran on Python 3.12, so it does not qualify 3.14 compatibility. | Recommended disposition: keep 3.12 policy, close this incompatible major-runtime bump in authorized execution and configure BK-28 update policy to avoid repeats. Alternative only if owner chooses 3.14: dedicated compatibility plan updating CI, image, docs and policy together with container/migration/browser proof. Never delete the policy assertion merely to green the PR. | Recorded policy-consistent closure/supersession; or explicitly approved new runtime lane with complete exact-head proof. A disposition resolves the PR without forcing a runtime upgrade. |

**Historical PR reconciliation:** #2 hardening, #4 UI, #5 enrichment and #6 marker research are merged and part of current source. #3 old Playwright, #8 old uvicorn and #10 old httpx2 are closed, superseded by #9/#11/#12 respectively. Do not reopen or reimplement them.

## Shared implementation and verification procedure

Each task below supplies affected files, contract, implementation, regression assertions and original issue acceptance. File paths are relative to the isolated source worktree; “(new)” marks planned test files. Other absent proposed files/folders are additions; never reconstruct a missing primary working tree. “Test BK-N” is a planned test/group label, not a claim that it exists.

1. Write the named regressions in the specified test files and prove the relevant failure on the maintained pre-change source. For a pilot, first record the required policy/sample selection; use synthetic fixtures until approved otherwise.
2. Implement the specified contract, using existing patterns and preserving the original scope boundaries in the frozen spec.
3. Run the focused test command: `.venv/bin/python -m pytest -q <listed test files>`. Where a listed file is tests/test_browser_smoke.py, include the complete operator flow, not just new tests. Record expected PASS plus the item-specific assertions.
4. Run frontend syntax when JavaScript changes. Run shared migration/verification fixtures for schema changes, and readiness/non-root image smoke for lifecycle/deploy/dependency changes.
5. Commit only explicitly named files in a focused change, e.g. `fix: close SQLite connections deterministically` for BK-01. Do not `git add -A` across primary residue. Create/update a PR with item mapping, test receipt, compatibility and rollback limits when publication is authorized.
6. Check exact PR head SHA and required CI results; after authorized merge, reverify combined source. Close an issue only when all acceptance requirements, including applicable manual/owner gates, are met. Record engineering-complete/acceptance-pending explicitly instead of claiming completion.

**Standard commands, from the maintained worktree:**

```sh
df -h /System/Volumes/Data
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q --ignore=tests/test_browser_smoke.py
.venv/bin/python -m pytest -q tests/test_browser_smoke.py
node --check app/static/app.js
git diff --check
docker build --tag bunkerkartet:ci .
```

Use the repository's requirements-dev.txt to prepare the isolated environment at execution time. Configure startup/smoke/restore fixtures with fresh synthetic data; never inherit the primary private data directory. Container smoke must start the built image as UID 10001 with a disposable writable volume and synthetic auth, then verify /api/ready and /api/version; a Docker build alone does not satisfy it. Disk check precedes installs/build loops.

### Migration and release protocol

Allocate next schema version against actual merged main. Each schema PR includes a nonempty previous-version fixture, current-version qualification, interrupted/resume test and hash/ID/history checks. Consolidate only adjacent compatible migration work; never silently reuse the editor's historical v9 number after another feature occupies it. Old image compatibility must be proved, not assumed: an old image rejecting newer schema requires restoring a compatible database backup or a forward repair, not merely changing an image tag.

Prepare reviewable release batches after each completed wave with SHA/digest, schema, migration evidence, backup/RPO boundary, deploy/readiness commands and recovery pair. Subsequent authorized deployment rechecks actual VPS ports, resolved SSH access, current backup success and target image metadata; uses Racknerd-Deploy and preserves rollback images. Live acceptance then exercises authenticated import preview/readback, revision conflict, edit/reload, approach/route/GPX and lock flows with owner-selected data. Optional ORS must remain distinguishable from required app readiness.

## Issue-by-issue implementation cards

The original acceptance text is retained below to prevent a simplified task from silently weakening the request. Priority is the proposal priority, not proof of a production incident.

### Wave 1 — Core correctness and safe tooling

<a id="bk-01-issue-13"></a>
#### BK-01 / [issue #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13) — Close SQLite connections deterministically

**Priority:** P1. **Required predecessors:** None. **Files:** `app/db.py`, `app/main.py`, `scripts/verify_database.py`. **Tests:** `tests/test_db.py`, `tests/test_api.py`, `tests/test_database_verify.py`.

**Contract:** Database.transaction() -> Iterator[sqlite3.Connection]; commits on success, rolls back on error, always closes.

- [ ] **Regressions — Test BK-01:** Retain the connection after success and exception: both reject execute; successful write persists, raised write rolls back, repeated calls close all tracked handles.
- [ ] **Implement:** Introduce Database.transaction() with contextmanager ownership; keep a raw opener only for explicitly owned callers, audit SQLite direct opens and materialize rows before close.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A retained connection is demonstrably closed after success and exception paths; writes commit/rollback as before; repeated synthetic requests leave bounded connection ownership; migration/evidence tests remain passing.

<a id="bk-02-issue-14"></a>
#### BK-02 / [issue #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14) — Require revisions for every protected edit and merge target

**Priority:** P1. **Required predecessors:** None. **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_api.py`, `tests/test_route_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Mandatory positive expected_revision; merge also requires target_expected_revision; stable 409 REVISION_MISMATCH code.

- [ ] **Regressions — Test BK-02:** Missing revision is 422; two different edits from one revision yield one success and one 409 with unchanged rows/events on conflict; independently changed merge target rejects.
- [ ] **Implement:** Make expected_revision mandatory on approach, review and adoption; add target_expected_revision for merge. Check both revisions inside the write transaction and include observed revisions in browser submissions.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Missing revisions fail validation; stale revisions return 409 without events or row changes; two distinct stale approach writes and a changed merge target reproduce one accepted write and one conflict; existing idempotent observations remain valid.

<a id="bk-03-issue-15"></a>
#### BK-03 / [issue #15](https://github.com/Reedtrullz/Bunkerkartet/issues/15) — Bound non-import mutations and validate numeric/text invariants

**Priority:** P1. **Required predecessors:** None. **Files:** `app/main.py`, `app/imports.py`. **Tests:** `tests/test_api.py`, `tests/test_imports.py`, `tests/test_import_api.py`.

**Contract:** Shared byte and field limits; safe 413/422 projections without input or credential echoes.

- [ ] **Regressions — Test BK-03:** Chunked oversized bodies receive 413 before writes; NaN/infinity, over-limit elements and whitespace fail safely; coordinate pairs and unknown-radius semantics remain intact.
- [ ] **Implement:** Extend streamed 2 MiB protection to JSON mutations before parsing. Reuse 30 warnings/1000 characters each and 20 related keys/300 characters each; reject whitespace-only required text and every non-finite number.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Streamed oversized PATCH/POST bodies receive 413; oversized warning/key elements and non-finite radius inputs receive controlled validation responses with no writes; normal historical imports and observations remain compatible; errors do not echo credential values.

<a id="bk-04-issue-16"></a>
#### BK-04 / [issue #16](https://github.com/Reedtrullz/Bunkerkartet/issues/16) — Make import preview exactly describe persisted candidate changes

**Priority:** P1. **Required predecessors:** None. **Files:** `app/main.py`. **Tests:** `tests/test_import_api.py`, `tests/test_imports.py`.

**Contract:** One candidate effect projection for _preview_record and _commit_package; original package hash remains unchanged.

- [ ] **Regressions — Test BK-04:** Preview/readback agree for whitespace, sparse updates, retained warnings and peer reordering; changed effects invalidate preview; exact 1.0 retry retains original receipt/hash.
- [ ] **Implement:** Build one effective candidate projection from existing state, normalized input and warnings; use it for both preview and commit, with no independent trimming/union logic.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Whitespace, sparse geometry, unknown access/confidence, retained warnings, and reordered peers yield the displayed effective values on readback; changed effects reject old previews; identical committed 1.0 packages still retry idempotently.

<a id="bk-06-issue-18"></a>
#### BK-06 / [issue #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18) — Apply latest-request-wins to all browser reads

**Priority:** P1. **Required predecessors:** None. **Files:** `app/static/app.js`. **Tests:** `tests/test_browser_smoke.py`.

**Contract:** Per-surface generation and auth epoch must both match before rendering; mutations are never blindly retried.

- [ ] **Regressions — Test BK-06:** Reverse two controlled responses: only newest renders; malformed 200 JSON shows recoverable stale/error state; superseded abort shows no alert; lock epoch wins every race.
- [ ] **Implement:** Add generation counters and AbortController per list, candidates, priority, detail and route read surface; enforce finite read timeouts and response-shape checks. Refresh hidden surfaces on demand.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Controlled delayed responses cannot revert newer filters, route selection, or lock state; malformed 200 JSON is a recoverable error; aborted reads do not show failure alerts; last-good data is visibly stale rather than silently current.

<a id="bk-08-issue-20"></a>
#### BK-08 / [issue #20](https://github.com/Reedtrullz/Bunkerkartet/issues/20) — Separate research shortlists from reviewed field destinations

**Priority:** P1. **Required predecessors:** None. **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_route_api.py`, `tests/test_api.py`, `tests/test_browser_smoke.py`.

**Contract:** route_eligible and route_blocking_reason derived from reviewed approach, never from structure access alone.

- [ ] **Regressions — Test BK-08:** Public candidate without reviewed approach is blocked; reviewed public viewpoint for dangerous structure remains allowed; merged/rejected/location-review records blocked on every entry surface.
- [ ] **Implement:** Reuse _route_site_error policy for API summaries and every route-entry button. Label research shortlist separately and return eligibility plus blocking reason based on reviewed approach and current review state.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Candidate imports with `access=public` but no reviewed approach are never presented as field-ready; a reviewed public viewpoint remains eligible even for a dangerous structure; no button bypasses rejected/merged/location-review guards; reasons are consistent in API and UI.

<a id="bk-10-issue-22"></a>
#### BK-10 / [issue #22](https://github.com/Reedtrullz/Bunkerkartet/issues/22) — Gate deployment on readiness and a verifiable release receipt

**Priority:** P1. **Required predecessors:** None. **Files:** `deploy/site.yml`, `deploy/templates/docker-compose.yml.j2`, `docker-compose.yml`, `.github/workflows/ci.yml`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_deploy_config.py`.

**Contract:** Release receipt binds commit, image digest, schema, backup checksum, selected port, readiness and rollback pair.

- [ ] **Regressions — Test BK-10:** Missing auth/backup/schema aborts before replacement; alternate port is used throughout; wrong SHA and not-ready fail; isolated rollback restores compatible image/database pair.
- [ ] **Implement:** Add staged preflight for env permissions, loopback port, SHA/digest labels, compatible schema and verified backup receipt. Require /api/ready and exact /api/version; retain recovery coordinates in a redacted release receipt.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Synthetic missing-auth/readiness failure cannot be reported as successful deploy; configured alternate port works consistently; backup/schema failures stop before replacement; an isolated rollback drill restores a compatible image/database pair with exact-version readiness.

<a id="bk-34-issue-46"></a>
#### BK-34 / [issue #46](https://github.com/Reedtrullz/Bunkerkartet/issues/46) — Preserve evidence identity and historical citation metadata

**Priority:** P2. **Required predecessors:** None. **Files:** `app/main.py`, `app/db.py`. **Tests:** `tests/test_import_api.py`, `tests/test_api.py`.

**Contract:** URL registry identity differs from dated evidence reading identity; unknown historical metadata stays unknown.

- [ ] **Regressions — Test BK-34:** Two link roles do not double-count evidence; different title/type readings retain identity; merge transfer and legacy rows preserve evidence IDs/excerpts/hash.
- [ ] **Implement:** Fix _source_rows identity selection to emit each evidence item once with separate roles; add item-level historical citation snapshot or recover from original recorded source index without rewriting payloads.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Multiple roles, legacy links, repeated imports and merge transfers yield unique evidence IDs; two historical titles remain attributable to their original package; genuinely distinct readings remain available; raw payloads/hashes are unchanged.

<a id="bk-35-issue-47"></a>
#### BK-35 / [issue #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47) — Require unique claim identities and stable references

**Priority:** P2. **Required predecessors:** None. **Files:** `app/enrichment.py`. **Tests:** `tests/test_enrichment.py`.

**Contract:** Stable claim ID is unique per site and never reused for unrelated text; existing editor validation reused.

- [ ] **Regressions — Test BK-35:** Duplicate IDs across sections rejected; valid seed unchanged; section move keeps ID; superseded/missing refs never rebound by position; repeated references not silently erased.
- [ ] **Implement:** Port the existing editor branch uniqueness validator now without its older seed/model replacement; reject whitespace IDs and duplicate references as safe validation. Define stable IDs through section move/supersession and preserve full projection when editor lands.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Duplicate IDs across different sections fail with a safe field-level error; existing valid documents round-trip; section changes preserve identity; references to superseded or missing claims are explicitly handled, not rebound by list position.

<a id="bk-36-issue-48"></a>
#### BK-36 / [issue #48](https://github.com/Reedtrullz/Bunkerkartet/issues/48) — Reject ambiguous JSON consistently at ingestion boundaries

**Priority:** P2. **Required predecessors:** None. **Files:** `app/json_input.py (new)`, `app/main.py`, `app/enrichment.py`, `research/validate_import.py`, `app/static/app.js`. **Tests:** `tests/test_json_input.py (new)`, `tests/test_import_api.py`, `tests/test_import_tool.py`, `tests/test_browser_smoke.py`.

**Contract:** decode_json_strict(raw: bytes, *, max_bytes: int, max_depth: int) -> object; default depth 64, existing import bytes 2 MiB; preserve canonical semantic hash.

- [ ] **Regressions — Test BK-36:** Top-level/nested duplicates fail in CLI/browser/API; nonstandard numbers/deep nesting bounded; accepted 1.0 payload hashes unchanged; errors omit excerpts.
- [ ] **Implement:** Create decode_json_strict for original UTF-8 input: reject duplicate keys/nonstandard numbers and pre-bound nesting. Submit original research file bytes to preview/commit so JSON.parse cannot erase ambiguity first.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Top-level/nested duplicate keys fail in CLI, browser and API; legacy clean retries keep their hashes; malformed/nested files fail boundedly without dumping private excerpts into errors; preview and commit consume identical accepted semantics.

<a id="bk-40-issue-52"></a>
#### BK-40 / [issue #52](https://github.com/Reedtrullz/Bunkerkartet/issues/52) — Make documented research setup and shell commands executable

**Priority:** P1. **Required predecessors:** None. **Files:** `README.md`, `research/README.md`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_research_quickstart.py (new)`.

**Contract:** Quickstart requires actual source checkout and declared runtime; placeholders never default to production mutation.

- [ ] **Regressions — Test BK-40:** Documented shell parses; preview/commit/readback and exact retry succeed locally; no production target or private WIP data used by examples.
- [ ] **Implement:** Fix both unclosed shell substitutions; make copyable commands target disposable local service, explain maintained ref/data-dir/runtime selection and validate snippets plus one synthetic idempotent import flow.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Shell snippets parse in the documented shell, the maintained source quickstart reaches one idempotent synthetic commit, and examples never default to an authenticated production write; existing WIP/database remain outside the test directory.

<a id="bk-42-issue-54"></a>
#### BK-42 / [issue #54](https://github.com/Reedtrullz/Bunkerkartet/issues/54) — Add owner-selected credential rotation and session locking

**Priority:** P2. **Required predecessors:** None. **Files:** `app/config.py`, `app/static/app.js`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_browser_smoke.py`, `tests/test_config.py`.

**Contract:** Owner-selected lock intervals and rotation boundary; existing memory-only credential model retained.

- [ ] **Regressions — Test BK-42:** Synthetic rotation rejects old token; lock erases private DOM/cache/drafts/object URLs; delayed callbacks fail epoch checks; ambiguous sends reconciled after reauth.
- [ ] **Implement:** Document replacement-token verification, old-token rejection and recovery; add configurable idle/visibility lock using existing clearAuthenticatedData and generation barriers. Keep intervals disabled until owner selection.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Synthetic rotation rejects the old credential after the chosen boundary; lock wipes private DOM/cache/object URLs and delayed callbacks cannot restore them; ambiguous sends are reconciled after reauth; token canaries never appear in logs, URLs or storage.

<a id="bk-21-issue-33"></a>
#### BK-21 / [issue #33](https://github.com/Reedtrullz/Bunkerkartet/issues/33) — Keep the private workspace usable when map assets fail

**Priority:** P2. **Required predecessors:** None. **Files:** `app/static/index.html`, `app/static/app.js`, `app/static/vendor/leaflet/ (new)`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_browser_smoke.py`, `tests/test_frontend_contract.py`.

**Contract:** Map capability is optional; list and private API initialization are independent of external tiles.

- [ ] **Regressions — Test BK-21:** Blocked CDN/tile network still permits auth/list/detail/review; retry adds no duplicate listeners/layers; local assets match upstream license/hash.
- [ ] **Implement:** Vendor exactly pinned Leaflet files/license with recorded integrity hashes; decouple authenticated list/detail initialization from map setup and add bounded tile failure/recovery/provider messaging.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Browser tests block CDN/tiles and still allow authentication, list/detail/review; errors distinguish map service from private API failure; recovery does not duplicate listeners/layers; vendored files match the pinned upstream release and license.

### Wave 2 — Persistence, editor and route foundations

<a id="bk-33-issue-45"></a>
#### BK-33 / [issue #45](https://github.com/Reedtrullz/Bunkerkartet/issues/45) — Qualify database structure and semantic invariants

**Priority:** P1. **Required predecessors:** [BK-01 / #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13). **Files:** `app/db.py`, `scripts/verify_database.py`, `app/main.py`. **Tests:** `tests/test_database_verify.py`, `tests/test_db.py`.

**Contract:** Versioned structural and semantic qualification; errors expose safe handles/categories without automatic repair.

- [ ] **Regressions — Test BK-33:** Same-column/wrong-key tables, missing request index, broken FK, bad coordinate pairs/revisions fail; valid old/current schemas distinguishable; verifier byte-preserves inspected files.
- [ ] **Implement:** Extend required_schema_errors with per-supported-version structural key/index/FK contracts and bounded semantic checks; share qualification with startup/deep readiness using read-only connections.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Same-name/wrong-key fixtures, missing observation request index and broken foreign keys fail; valid legacy/current fixtures remain distinguishable; genuine corruption is not accepted as ready; scans never mutate inspected files.

<a id="bk-37-issue-49"></a>
#### BK-37 / [issue #49](https://github.com/Reedtrullz/Bunkerkartet/issues/49) — Distinguish corrupt stored records from valid empty data

**Priority:** P1. **Required predecessors:** [BK-33 / #45](https://github.com/Reedtrullz/Bunkerkartet/issues/45). **Files:** `app/db.py`, `app/main.py`, `app/enrichment.py`. **Tests:** `tests/test_stored_record_decoding.py (new)`, `tests/test_api.py`, `tests/test_route_api.py`.

**Contract:** Typed persisted-data states consumed by read, verifier and protected actions; no silent regeneration/repair.

- [ ] **Regressions — Test BK-37:** Bad row does not conceal healthy rows; malformed/wrong-shape vs absent/empty distinct; corrupt route cannot export as valid; no raw payload logging.
- [ ] **Implement:** Decode consequential stored JSON against explicit field shapes; distinguish absent legacy, valid empty, malformed and unavailable. Return bounded per-record unavailable states while refusing corrupted geometry/GPX/trust actions.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Invalid JSON, wrong-shape JSON, missing legacy fields and valid empty values have separate fixtures/states; one bad historical row does not conceal all healthy rows; unavailable records cannot be adopted/exported as verified data; logs contain no raw private payload.

<a id="bk-38-issue-50"></a>
#### BK-38 / [issue #50](https://github.com/Reedtrullz/Bunkerkartet/issues/50) — Qualify every migration boundary with interruption fixtures

**Priority:** P2. **Required predecessors:** [BK-33 / #45](https://github.com/Reedtrullz/Bunkerkartet/issues/45). **Files:** `app/db.py`, `tests/fixtures/migrations/ (new)`. **Tests:** `tests/test_migration_boundaries.py (new)`, `tests/test_db.py`, `tests/test_database_verify.py`.

**Contract:** Single migration sequence with explicit committed boundaries; shared fixtures reused by all later schema changes.

- [ ] **Regressions — Test BK-38:** Rollback or documented committed boundary only; resume preserves row IDs/hashes/history; future versions unchanged; qualification passes after success.
- [ ] **Implement:** Build nonempty fixtures for each supported starting version and fail at DDL/evidence-copy/relation/version boundaries; prove resume behavior and FK enforcement after reconstruction.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Every supported boundary rolls back or stops at a documented committed version, then resumes correctly; no duplicate history or lost IDs/hashes; future versions fail without writes; verifier qualification passes after successful completion.

<a id="bk-39-issue-51"></a>
#### BK-39 / [issue #51](https://github.com/Reedtrullz/Bunkerkartet/issues/51) — Separate app construction from database startup side effects

**Priority:** P2. **Required predecessors:** [BK-01 / #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13). **Files:** `app/main.py`, `tests/browser_server.py`, `Dockerfile`. **Tests:** `tests/test_app_lifecycle.py (new)`, `tests/test_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Construction installs routes/settings; lifespan owns initialization once and connection cleanup.

- [ ] **Regressions — Test BK-39:** Module import with protected nonexistent path creates nothing; startup initializes only chosen synthetic path; instances isolated; failure stays fail-closed; container entrypoint starts correctly.
- [ ] **Implement:** Keep create_app/settings construction inert and move initialization to lifespan startup; update fixture seeding to run after startup and retain app.main:app compatibility unless a tested factory transition is needed.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Importing the module with a protected nonexistent data path creates nothing; explicitly started app initializes its selected synthetic directory; multiple test instances do not share defaults; startup failures remain fail-closed and container entrypoint smoke passes.

<a id="bk-25-issue-37"></a>
#### BK-25 / [issue #37](https://github.com/Reedtrullz/Bunkerkartet/issues/37) — Turn restore instructions into a recoverable verified procedure

**Priority:** P1. **Required predecessors:** [BK-01 / #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13). **Files:** `scripts/restore_database.py (new)`, `scripts/verify_database.py`, `scripts/verify_restore_archive.py`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_restore_procedure.py (new)`, `tests/test_restore_archive.py`, `tests/test_database_verify.py`.

**Contract:** Explicit staged restore states and rollback pair; uncertain post-backup writes require owner recovery decision.

- [ ] **Regressions — Test BK-25:** Nonempty synthetic archive restores and writes as app UID; inject every extraction/copy/start/readiness failure and prove retained rollback; wrong/future/oversize fails before replacement.
- [ ] **Implement:** Implement staged inspect/extract/qualify/permission-check/swap/start/readiness procedure; retain previous volume/image and handlers until exact-version success. Test stopped-writer/WAL capture and UID 10001 write permission.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A nonempty synthetic catalogue/WAL set restores under UID 10001 and accepts a synthetic transaction; injected extraction/copy/restart/readiness failures retain a usable rollback point; future/wrong schemas and oversized archives fail before replacement; cleanup occurs only after verified success or recorded recovery.

<a id="bk-07-issue-19"></a>
#### BK-07 / [issue #19](https://github.com/Reedtrullz/Bunkerkartet/issues/19) — Bind route results to an immutable route draft

**Priority:** P1. **Required predecessors:** [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18). **Files:** `app/main.py`, `app/db.py`, `app/static/app.js`. **Tests:** `tests/test_route_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Immutable route draft generation plus server request_id/payload_hash; snapshot rendering independent of siteCache.

- [ ] **Regressions — Test BK-07:** Delayed calculation plus reorder/remove/start change cannot install old GPX; filtered-out saved stops display; identical retries save one route; changed payload under same ID conflicts; failure releases reservation/slots.
- [ ] **Implement:** Capture immutable name/start/ordered-stop drafts, invalidate geometry and GPX on any draft edit, render saved stops from snapshots. Add request-ID/payload-hash reservation with bounded pending state outside provider-held write transactions.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Slow calculation plus reorder/start/remove cannot yield apparently matching stale GPX; filtered-out saved stops remain visible; changed approach warning persists; repeated identical route request yields one saved plan; provider failures release request/limiter state.

<a id="bk-09-issue-21"></a>
#### BK-09 / [issue #21](https://github.com/Reedtrullz/Bunkerkartet/issues/21) — Validate route-provider ordering and expose bounded failure states

**Priority:** P2. **Required predecessors:** None. **Files:** `app/routes.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_routes.py`, `tests/test_route_api.py`.

**Contract:** normalize_ors_response receives requested waypoint count; stable provider codes and optional bounded retry guidance; automatic creation retry waits for BK-07.

- [ ] **Regressions — Test BK-09:** Reversed/wrong-count indices, malformed values, busy/quota/timeout fixtures fail safely; semaphore releases on all paths; missing indices retain snapping uncertainty warning.
- [ ] **Implement:** Qualify waypoint count/order against requested stops, reject bool/nonnumeric provider data and expose a small redacted error taxonomy with bounded Retry-After. Preserve current two-slot limiter and stdlib transport.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Invalid ordering/count and malformed finite values fail controllably; timeouts/quota responses do not expose provider credentials/body; semaphore slots always release; valid missing-index routes retain the existing uncontrolled-snapping warning.

<a id="bk-11-issue-23"></a>
#### BK-11 / [issue #23](https://github.com/Reedtrullz/Bunkerkartet/issues/23) — Make operator-flow browser tests isolated and diagnosable

**Priority:** P1. **Required predecessors:** [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18), [BK-07 / #19](https://github.com/Reedtrullz/Bunkerkartet/issues/19). **Files:** `tests/test_browser_smoke.py`, `tests/browser_server.py`, `.github/workflows/ci.yml`. **Tests:** `tests/test_browser_smoke.py`.

**Contract:** Browser fixtures own isolated synthetic state; artifacts capped and retained only on failure; never weaken the saved-GPX assertion.

- [ ] **Regressions — Test BK-11:** Complete flow passes repeated and reordered runs without shared state; injected delay/failure produces redacted trace/screenshot/server output; compare httpx2 versions under identical Playwright/runtime.
- [ ] **Implement:** First add failure artifacts and per-test synthetic database/server state, then reproduce the route-button visibility timeout with response-status and surface/render assertions. Add deterministic BK-06/07 races after those contracts land.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Exact failing scenario has a documented reproduction or clearly bounded non-reproduction; tests pass in reordered/repeated execution without shared mutation assumptions; injected faults yield actionable artifacts; no real catalogue or credentials enter artifacts.

<a id="bk-12-issue-24"></a>
#### BK-12 / [issue #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24) — Integrate the existing persistent content editor without regressions

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-04 / #16](https://github.com/Reedtrullz/Bunkerkartet/issues/16). **Files:** `app/db.py`, `app/enrichment.py`, `app/main.py`, `app/static/app.js`, `app/static/styles.css`. **Tests:** `tests/test_db.py`, `tests/test_enrichment.py`, `tests/test_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Effective content = valid persisted override or current seed; external_key is server-bound; next schema number allocated at integration time.

- [ ] **Regressions — Test BK-12:** Nonempty main v8 fixture migrates; all main research states and seed keys remain; restart preserves overrides; unused sources/claim IDs round-trip; stale edits reject; malformed override fails closed.
- [ ] **Implement:** Selectively port editor code from 0890bdd; keep main seed JSON and curation policy. Integrate stored overrides, complete source/claim projection and editor drafts with the new revision/import rules.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Real v8 fixture migrates to v9; current seed loads unchanged with all research states; persisted overrides survive restart; sources and claim IDs round-trip; stale edits fail; malformed stored content fails closed; rollback compatibility is documented and verified synthetically.

<a id="bk-18-issue-30"></a>
#### BK-18 / [issue #30](https://github.com/Reedtrullz/Bunkerkartet/issues/30) — Show feature, approach, entrance and observation roles distinctly

**Priority:** P2. **Required predecessors:** [BK-03 / #15](https://github.com/Reedtrullz/Bunkerkartet/issues/15). **Files:** `app/main.py`, `app/static/app.js`, `app/static/styles.css`. **Tests:** `tests/test_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Role/radius travel as properties; WGS84 feature and observation points remain separate.

- [ ] **Regressions — Test BK-18:** Feature/entrance/viewpoint/approach distinguishable without color; no guessed radius; GeoJSON role retained; route targets only reviewed approach.
- [ ] **Implement:** Expose feature/approach/observation role and explicitly recorded radius; add separate Leaflet layers, shapes/text labels and role detail links, including requested-versus-snapped approach.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A synthetic feature/entrance/viewpoint/approach set is distinguishable without color alone; radius appears only when explicitly recorded; exports preserve role semantics; routes still target reviewed approaches, never merely the visually closest point.

<a id="bk-50-issue-62"></a>
#### BK-50 / [issue #62](https://github.com/Reedtrullz/Bunkerkartet/issues/62) — Validate GPX and GeoJSON as actual interchange artifacts

**Priority:** P2. **Required predecessors:** [BK-03 / #15](https://github.com/Reedtrullz/Bunkerkartet/issues/15). **Files:** `app/routes.py`, `app/main.py`, `tests/fixtures/gpx/ (new)`. **Tests:** `tests/test_routes.py`, `tests/test_route_api.py`, `tests/test_geojson_export.py (new)`.

**Contract:** Export-safe labels never rewrite stored evidence; invalid data fails controlledly rather than claiming valid interchange.

- [ ] **Regressions — Test BK-50:** Unicode/markup serialize legally; illegal XML controls fail explicitly; GPX meets schema; GeoJSON axis/IDs/null retained; boundary policy and legacy routes covered.
- [ ] **Implement:** Replace handcrafted XML with stdlib serializer and explicit invalid-character rejection; pin GPX XSD fixture for validation. Check GeoJSON coordinates/null geometry/IDs/roles; normalize +180 to -180 at export only.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Markup, Unicode, control-character and boundary fixtures have explicit outcomes; exported GPX parses and satisfies its schema; GeoJSON retains stable IDs, null geometry and correct axis order; ordinary saved routes remain compatible.

<a id="bk-20-issue-32"></a>
#### BK-20 / [issue #32](https://github.com/Reedtrullz/Bunkerkartet/issues/32) — Preserve unsaved drafts and resolve revision conflicts deliberately

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18). **Files:** `app/static/app.js`. **Tests:** `tests/test_browser_smoke.py`.

**Contract:** Draft store is memory-only; request identity retained for unresolved submissions, never bearer persistence.

- [ ] **Regressions — Test BK-20:** Navigation/transient failure preserves draft; one successful send clears only its draft; stale conflict never overwrites; lock removes all drafts and late callbacks cannot revive them.
- [ ] **Implement:** Keep bounded session drafts per site/form; prompt on deliberate discard, compare current/draft changed fields on 409, and preserve observation request identity until ambiguous outcome readback.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Navigation and transient errors preserve unsent work within the session; stale reload never silently overwrites current data; successful sends clear only the corresponding draft; lock erases all private state and delayed callbacks cannot restore it.

<a id="bk-41-issue-53"></a>
#### BK-41 / [issue #53](https://github.com/Reedtrullz/Bunkerkartet/issues/53) — Publish a tested API contract for authenticated clients

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-03 / #15](https://github.com/Reedtrullz/Bunkerkartet/issues/15). **Files:** `app/main.py`, `app/imports.py`, `docs/API_CONTRACT.md (new)`. **Tests:** `tests/test_api_contract.py (new)`.

**Contract:** API contract consumes mandatory revision and bounds from BK-02/03; document compatibility path for older clients.

- [ ] **Regressions — Test BK-41:** Synthetic authenticated client discovers auth, reads/mutates safely; missing/stale revision behavior and null/unknown round-trip; incompatible schema drift detected; no private examples.
- [ ] **Implement:** Add real bearer security scheme and response models for actual list/detail/mutation/import/history clients; contract version independent of build SHA and explicit revision/unknown/error shapes.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A synthetic client can discover auth and round-trip supported operations; null/unknown and 409/422 semantics are explicit; incompatible schema drift fails a focused check; legacy clients have a documented compatibility path.

### Wave 3 — Research workflows and operational controls

<a id="bk-05-issue-17"></a>
#### BK-05 / [issue #17](https://github.com/Reedtrullz/Bunkerkartet/issues/17) — Make Norwegian search cover actual evidence and curated content

**Priority:** P2. **Required predecessors:** None. **Files:** `app/main.py`, `app/enrichment.py`. **Tests:** `tests/test_api.py`, `tests/test_enrichment.py`.

**Contract:** Search consumes effective content and dated evidence readings; no source fetching or semantic index.

- [ ] **Regressions — Test BK-05:** Æ/Ø/Å pairs, subsequent excerpts for one URL, edited names and claim text match under the same filters; rejected/merged visibility and literal wildcard policy hold.
- [ ] **Implement:** Apply casefold normalization consistently to effective names, claims and evidence_items excerpts; make literal percent/underscore matching explicit. Re-run against persisted overrides after BK-12 lands.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Synthetic `Æ/Ø/Å` upper/lower pairs, later same-URL excerpts, edited display names, and claim text are discoverable under the same filters; `%` and `_` behavior is documented/tested; rejected/merged visibility rules remain unchanged.

<a id="bk-13-issue-25"></a>
#### BK-13 / [issue #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25) — Persist a private question-oriented curator queue

**Priority:** P2. **Required predecessors:** [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_curator_queue.py (new)`, `tests/test_browser_smoke.py`.

**Contract:** Private curator question identity/revision shared by merge, lifecycle, hypotheses and visits.

- [ ] **Regressions — Test BK-13:** Two questions on one site remain independent; defer retains references; only explicit revisioned decision resolves; ordinary exports omit queue and private discussion.
- [ ] **Implement:** Add bounded private question records keyed by stable site/external key with sources, uncertainty, proposed action, disposition, reason and revision; allow not-yet-imported keys without creating sites.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Multiple independent questions attach to one site; deferral retains evidence; changes are revisioned/audited; only an explicit curator decision resolves an item; the public repository and ordinary exports omit private queue material.

<a id="bk-15-issue-27"></a>
#### BK-15 / [issue #27](https://github.com/Reedtrullz/Bunkerkartet/issues/27) — Version import evidence metadata without rewriting 1.0 history

**Priority:** P2. **Required predecessors:** [BK-04 / #16](https://github.com/Reedtrullz/Bunkerkartet/issues/16). **Files:** `app/imports.py`, `app/main.py`, `research/validate_import.py`, `research/llm-import-prompt.md`. **Tests:** `tests/test_imports.py`, `tests/test_import_api.py`, `tests/test_import_tool.py`.

**Contract:** Versioned import decoder accepts 1.0 and 1.1 explicitly; absent metadata is unknown, not inferred.

- [ ] **Regressions — Test BK-15:** Original 1.0 fixtures/retries retain hashes; 1.1 preview and readback preserve metadata; unknown rights stay unknown; ordinary exports omit private annotations.
- [ ] **Implement:** Add import schema 1.1 for bounded evidence kind/role, claim links, uncertainty and rights notes; dispatch 1.0 through unchanged parsing/hash semantics and generate schema/docs from the models.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Original fixtures/exact retries preserve their historical identity; new evidence round-trips and is visible before commit; absent rights are shown as unknown rather than permission; private review annotations are not included in ordinary catalogue exports.

<a id="bk-16-issue-28"></a>
#### BK-16 / [issue #28](https://github.com/Reedtrullz/Bunkerkartet/issues/28) — Track source/access freshness and explicit re-review decisions

**Priority:** P2. **Required predecessors:** [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_freshness.py (new)`, `tests/test_route_api.py`.

**Contract:** Freshness is independent from lifecycle/access; enabling an expiry interval requires owner policy.

- [ ] **Regressions — Test BK-16:** Date-controlled due/overdue/unknown outcomes hold; explicit suspension blocks new routes; saved snapshots remain with current-state warnings; reads do not rewrite trust.
- [ ] **Implement:** Store explicit review date/state and claim currentness; expose due/unknown states with injected clock. Prepare configurable policies and curator suspension of an approach, linked to a question.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Synthetic date-controlled tests show due/overdue/unknown states; access-sensitive invalidation blocks new routes; saved routes show historical snapshots plus changed-current-state warnings; read-only viewing does not auto-promote or rewrite evidence.

<a id="bk-44-issue-56"></a>
#### BK-44 / [issue #56](https://github.com/Reedtrullz/Bunkerkartet/issues/56) — Expose an authenticated import ledger and commit receipts

**Priority:** P2. **Required predecessors:** [BK-04 / #16](https://github.com/Reedtrullz/Bunkerkartet/issues/16), [BK-34 / #46](https://github.com/Reedtrullz/Bunkerkartet/issues/46). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_import_ledger.py (new)`, `tests/test_import_api.py`.

**Contract:** Commit receipt binds batch ID, original payload hash and persisted record/evidence effects; raw payload excluded by default.

- [ ] **Regressions — Test BK-44:** Exact retry returns same receipt; changed payload conflicts; later review/merge still traceable; auth required; source-link and evidence counts separate.
- [ ] **Implement:** Expose authenticated stable-cursor batch summaries/detail and immutable effect receipts with evidence IDs. Support lookup by original batch identity after ambiguous commit responses.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Exact retries resolve to the same receipt; conflicting payloads remain rejected; record effects are traceable after later review/merge; unauthenticated access fails; aggregate counts distinguish source links from retained evidence items.

<a id="bk-49-issue-61"></a>
#### BK-49 / [issue #61](https://github.com/Reedtrullz/Bunkerkartet/issues/61) — Attach pinpoint archival locators to curated citations

**Priority:** P2. **Required predecessors:** [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47). **Files:** `app/enrichment.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_enrichment.py`, `tests/test_dossiers.py (new)`.

**Contract:** Citation identity includes claim ID plus source ID and optional locator; locator is not evidence of reading.

- [ ] **Regressions — Test BK-49:** Two claims cite different pages of same source; missing locator stays absent; exact text survives editor/JSON/print; private identifiers only in selected private outputs.
- [ ] **Implement:** Add optional bounded citation-level archive reference/page/figure/map-sheet/quotation/rights metadata; preserve spelling, not just source registry URL. Round-trip through editor and private dossier.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Two claims can cite different pages of one source; unknown locators remain absent, not guessed; editor/print/JSON round-trip the exact reference; private identifiers are included only in approved private outputs.

<a id="bk-52-issue-64"></a>
#### BK-52 / [issue #64](https://github.com/Reedtrullz/Bunkerkartet/issues/64) — Navigate a private source registry across linked sites and claims

**Priority:** P2. **Required predecessors:** [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24), [BK-34 / #46](https://github.com/Reedtrullz/Bunkerkartet/issues/46), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_source_registry.py (new)`.

**Contract:** Source registry navigation preserves original URL and evidence identity; read reconciliation does not merge namespaces.

- [ ] **Regressions — Test BK-52:** Shared URL readings discoverable with unique evidence counts; unused sources retained; namespace mismatches explicit; source re-review never bulk-demotes sites.
- [ ] **Implement:** Add private source-centric filters and links to claims/sites/reading IDs, reconciling registry and overlay references on read. Include unused overlay sources and human queue suggestions.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** One URL's linked claims/readings are discoverable without double-counting evidence; unused overlay sources remain visible as unused, not deleted; updating source review creates explicit follow-up suggestions rather than auto-demoting all dependent sites.

<a id="bk-43-issue-55"></a>
#### BK-43 / [issue #55](https://github.com/Reedtrullz/Bunkerkartet/issues/55) — Make third-party map egress an explicit workspace choice

**Priority:** P2. **Required predecessors:** [BK-21 / #33](https://github.com/Reedtrullz/Bunkerkartet/issues/33). **Files:** `app/config.py`, `app/static/app.js`, `app/static/index.html`. **Tests:** `tests/test_browser_smoke.py`.

**Contract:** Owner basemap choice precedes third-party tile network; attribution retained.

- [ ] **Regressions — Test BK-43:** Network interception sees no tile calls in no-map mode before/after auth; provider switch removes old layer; lock/reload obey policy; private list flows continue.
- [ ] **Implement:** Gate external tile initialization behind explicit basemap/provider selection; implement functional no-external-map mode and concise egress state, separate from explicit routing/start disclosure.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Browser network interception proves zero tile requests in no-map mode, including before auth; changing provider stops new requests to the old layer; lock/rehydration obey the selected policy; private list operations do not depend on third-party traffic.

<a id="bk-22-issue-34"></a>
#### BK-22 / [issue #34](https://github.com/Reedtrullz/Bunkerkartet/issues/34) — Finish form accessibility and mobile field ergonomics

**Priority:** P2. **Required predecessors:** [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18). **Files:** `app/static/app.js`, `app/static/index.html`, `app/static/styles.css`, `.github/workflows/ci.yml`. **Tests:** `tests/test_browser_smoke.py`.

**Contract:** Form error locations map to labeled DOM controls; automated checks and manual assistive-technology acceptance tracked separately.

- [ ] **Regressions — Test BK-22:** 422 approach/location errors focus correct input; keyboard import/review/route works; 320px/landscape/200% text has no obscured controls; manual screen-reader checklist remains explicit.
- [ ] **Implement:** Name all controls, connect field errors/descriptions and first-error focus; retain privacy/focus reset. Add compact field view and selected Firefox/WebKit/mobile/large-text coverage.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Approach/location-review 422 errors identify/focus the correct field; keyboard-only import/review/route workflows remain usable; representative mobile layouts have no obscured controls or horizontal overflow; report automated versus manual acceptance separately.

<a id="bk-23-issue-35"></a>
#### BK-23 / [issue #35](https://github.com/Reedtrullz/Bunkerkartet/issues/35) — Add stable category filters and private navigation state

**Priority:** P2. **Required predecessors:** [BK-05 / #17](https://github.com/Reedtrullz/Bunkerkartet/issues/17), [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18). **Files:** `app/static/app.js`, `app/static/index.html`. **Tests:** `tests/test_browser_smoke.py`, `tests/test_frontend_contract.py`.

**Contract:** Private navigation holds allowlisted identifiers/filter categories only; map center remains view state.

- [ ] **Regressions — Test BK-23:** English/Norwegian aliases agree; rejected sites reachable for protected review; history restores selection after auth; URL canaries exclude tokens, notes, evidence and personal starts.
- [ ] **Implement:** Define small tested category alias map while retaining raw types; restore surface/site/filter with back-forward state and navigable related keys; expose rejected-review navigation.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Norwegian and English category aliases classify consistently; raw type remains inspectable; back/forward restores the intended surface/selection after auth; rejected records can be reviewed/restored without manually calling the API; URLs never contain credentials or personal route starts.

<a id="bk-26-issue-38"></a>
#### BK-26 / [issue #38](https://github.com/Reedtrullz/Bunkerkartet/issues/38) — Establish bounded backup and private-data retention operations

**Priority:** P2. **Required predecessors:** [BK-25 / #37](https://github.com/Reedtrullz/Bunkerkartet/issues/37). **Files:** `scripts/backup_database.py (new)`, `deploy/backup/ (new)`, `docs/DEPLOYMENT.md`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_backup_retention.py (new)`, `tests/test_restore_procedure.py (new)`.

**Contract:** Owner-selected backup/retention policy and verified receipts; no schedules, external transfers or deletion defaults enabled by this plan.

- [ ] **Regressions — Test BK-26:** Missed/failed backup detectable; approved offsite sample restores within selected RPO/RTO; deletion preview matches removed route start/geometry/GPX; evidence/history untouched; backup purge lag documented.
- [ ] **Implement:** Inventory real host tooling before scheduling; encode selected RPO/RTO/retention in config, backup checksums/encryption/offsite receipt and synthetic restore drill. Add exact route-deletion/expiry preview separately from durable evidence.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A controlled missed/failed backup produces a meaningful alert; a sampled offsite archive restores and meets chosen RPO/RTO; deletion preview lists exact private records; route deletion removes start/geometry/GPX as specified without deleting site evidence; retention/readback is auditable.

<a id="bk-27-issue-39"></a>
#### BK-27 / [issue #39](https://github.com/Reedtrullz/Bunkerkartet/issues/39) — Add redacted operational diagnostics without tracking visitors

**Priority:** P2. **Required predecessors:** [BK-01 / #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13), [BK-09 / #21](https://github.com/Reedtrullz/Bunkerkartet/issues/21), [BK-10 / #22](https://github.com/Reedtrullz/Bunkerkartet/issues/22). **Files:** `app/main.py`, `app/routes.py`, `app/config.py`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_diagnostics.py (new)`.

**Contract:** Low-cardinality operational events only; separating shallow/deep checks must preserve explicit qualification semantics.

- [ ] **Regressions — Test BK-27:** Injected failures correlate safe code/request ID; canaries for auth/body/URLs/starts/notes absent; scan cost includes fixture/hardware; no visitor tracking.
- [ ] **Implement:** Add bounded request-correlation logs for import/route/contention/startup/readiness and measure readiness scan cost on synthetic sizes; redact sensitive values before logging and bound rotation/retention.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Injected provider/database errors can be correlated to a safe error code and request ID; logs/artifacts pass credential/private-content canaries; rotation/retention is bounded; readiness cost is reported with dataset/hardware rather than an unsupported latency claim.

<a id="bk-28-issue-40"></a>
#### BK-28 / [issue #40](https://github.com/Reedtrullz/Bunkerkartet/issues/40) — Enforce release governance and runtime-aware dependency review

**Priority:** P2. **Required predecessors:** [BK-10 / #22](https://github.com/Reedtrullz/Bunkerkartet/issues/22), [BK-11 / #23](https://github.com/Reedtrullz/Bunkerkartet/issues/23). **Files:** `.github/workflows/ci.yml`, `.github/dependabot.yml`, `Dockerfile`, `docs/DEPLOYMENT.md`. **Tests:** `tests/test_deploy_config.py`, `tests/test_container_smoke.py (new)`.

**Contract:** Release governance consumes readiness/browser proof; branch rules and major runtime lane are owner decisions, not inferred from green checks.

- [ ] **Regressions — Test BK-28:** CI/image/runtime agree; non-root image starts on synthetic volume; branch rule dry-run and selected policy listed; only authorized subsequent configuration enables restrictions.
- [ ] **Implement:** Retain Python 3.12 lane and immutable pins; add dependency-resolution receipt and non-root synthetic container readiness smoke. Prepare exact required-check/bypass policy and dependency PR resolution receipts.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Unauthorized/unverified merges are blocked under the approved policy; runtime policy, base image and CI agree; existing version updates retain their own PRs; built container starts with synthetic data under intended UID and reaches readiness; artifact provenance is redacted and retained.

### Wave 4 — Consequential review and content history

<a id="bk-14-issue-26"></a>
#### BK-14 / [issue #26](https://github.com/Reedtrullz/Bunkerkartet/issues/26) — Preview merges and preserve identity lineage

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_merge_preview.py (new)`, `tests/test_api.py`.

**Contract:** Merge preview hash binds source revision, target revision, survivor identity and proposed transfers; append lineage rather than rewriting history.

- [ ] **Regressions — Test BK-14:** Preview equals committed effect; stale source/target rejects without writes; historical keys/evidence/request IDs remain traceable; self-relations rejected or explicitly resolved; no automatic merge.
- [ ] **Implement:** Add side-by-side merge effect preview bound to both revisions, survivor and reason; retain original-key aliases/lineage and list transfer/conflict/self-relation outcomes before commit.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Preview and commit effects agree; changed source/target produces 409; evidence/request IDs/history remain traceable; original keys resolve to an explicitly merged identity; unrelated/ambiguous records remain separate.

<a id="bk-17-issue-29"></a>
#### BK-17 / [issue #29](https://github.com/Reedtrullz/Bunkerkartet/issues/29) — Bind lifecycle promotion to named evidence and a review reason

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25). **Files:** `app/main.py`, `app/static/app.js`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_api.py`, `tests/test_curator_queue.py (new)`.

**Contract:** Review decision records reviewed revision, reason and named support; lifecycle, identity, geometry and permission remain separate.

- [ ] **Regressions — Test BK-17:** Missing reason/reference fails; addressed questions and evidence are recorded; old statuses remain readable without migration promotion/demotion; viewpoint review never implies entry.
- [ ] **Implement:** Require review reason and selected immutable evidence/observation references for promotions, bound to revision and unresolved identity/location questions; clarify public-viewpoint verification semantics.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Promotion without required rationale/evidence fails; unresolved identity or location questions are explicitly addressed before confirmation; events explain the decision; old statuses remain readable and are not automatically demoted/promoted during migration.

<a id="bk-19-issue-31"></a>
#### BK-19 / [issue #31](https://github.com/Reedtrullz/Bunkerkartet/issues/31) — Correct observations through append-only amendments

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-17 / #29](https://github.com/Reedtrullz/Bunkerkartet/issues/29). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_observation_amendments.py (new)`, `tests/test_api.py`.

**Contract:** Observation identity stays immutable; effective amendment chain and support-invalidated state consumed by visits/history.

- [ ] **Regressions — Test BK-19:** Original row/events unchanged; identical amendment retry deduplicates; stale amendment conflicts; withdrawn support cannot justify a new promotion; UI distinguishes original/effective.
- [ ] **Implement:** Append reasoned, revisioned, request-idempotent amendments to immutable observations; compute effective view and flag evidence-dependent decisions for re-review without changing adopted coordinates automatically.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Corrections are traceable, idempotent and conflict-safe; original events remain inspectable; withdrawn supporting evidence cannot silently justify new promotion; the browser clearly labels original versus effective assessment.

<a id="bk-46-issue-58"></a>
#### BK-46 / [issue #58](https://github.com/Reedtrullz/Bunkerkartet/issues/58) — Compare revision history and stage compensating edits

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24), [BK-44 / #56](https://github.com/Reedtrullz/Bunkerkartet/issues/56). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_revision_history.py (new)`, `tests/test_api.py`.

**Contract:** Compensation is a new ordinary edit referencing prior event; protected decisions use their own workflow.

- [ ] **Regressions — Test BK-46:** No duplicates/omissions across cursor; missing vs empty diff explicit; stale restoration conflicts; text restoration cannot reset lifecycle/location/access or unmerge.
- [ ] **Implement:** Add stable history cursor and field/claim diff with receipt links. Stage ordinary historical text as a fresh expected_revision draft and record a compensating event; label gaps.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Older history remains reachable without duplicate/omitted IDs; comparisons distinguish missing from empty values; a stale compensating draft conflicts; restoring ordinary text never bypasses source, location-review or lifecycle guards.

<a id="bk-60-issue-72"></a>
#### BK-60 / [issue #72](https://github.com/Reedtrullz/Bunkerkartet/issues/72) — Reconcile new seed content against persistent curator overrides

**Priority:** P2. **Required predecessors:** [BK-12 / #24](https://github.com/Reedtrullz/Bunkerkartet/issues/24), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47), [BK-46 / #58](https://github.com/Reedtrullz/Bunkerkartet/issues/58). **Files:** `app/db.py`, `app/enrichment.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_seed_reconciliation.py (new)`, `tests/test_enrichment.py`.

**Contract:** Reconciliation uses normal expected_revision editing and receipt; code deploy never changes trust/content automatically.

- [ ] **Regressions — Test BK-60:** Disjoint seed additions appear as proposals; conflicting local edit intact; removed referenced source needs decision; failed/stale reconcile leaves original; legacy unknown base explicit.
- [ ] **Implement:** Record base seed hash/version for overrides; compare base/current/local using stable claim/source IDs and stage three-way reconciliation with per-item owner choice.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Disjoint new seed claims are visible as proposals, conflicting local edits stay intact, removed referenced sources require a decision, and failed reconciliation preserves the original override; receipt identifies reviewed versions and resulting revision.

<a id="bk-24-issue-36"></a>
#### BK-24 / [issue #36](https://github.com/Reedtrullz/Bunkerkartet/issues/36) — Export selected private trip dossiers with provenance

**Priority:** P2. **Required predecessors:** [BK-07 / #19](https://github.com/Reedtrullz/Bunkerkartet/issues/19), [BK-08 / #20](https://github.com/Reedtrullz/Bunkerkartet/issues/20), [BK-16 / #28](https://github.com/Reedtrullz/Bunkerkartet/issues/28). **Files:** `app/main.py`, `app/static/app.js`, `app/static/dossier.css (new)`. **Tests:** `tests/test_dossiers.py (new)`, `tests/test_browser_smoke.py`.

**Contract:** Private dossier selection is not backup or publication; field allowlist and membership are bound to preview.

- [ ] **Regressions — Test BK-24:** Preview/download agree; unknown geometry remains null; default excludes private questions/observations/personal start; print preserves currentness and permission caveats; no trust mutation.
- [ ] **Implement:** Generate selected printable HTML and versioned JSON dossier with timestamp, source links, uncertainty and approach snapshot plus GPX/GeoJSON. Preview membership/fields and make sensitive inclusions explicit opt-ins.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Preview and downloaded membership/fields match; unknown geometry remains null; sensitive defaults exclude private queue/observation material and precise personal start; timestamps/currentness and permission caveats survive print/export; dossiers never promote statuses.

### Wave 5 — Bounded advanced workflows and verification

<a id="bk-29-issue-41"></a>
#### BK-29 / [issue #41](https://github.com/Reedtrullz/Bunkerkartet/issues/41) — Measure catalogue growth and remove proven query/render repetition

**Priority:** P3. **Required predecessors:** [BK-01 / #13](https://github.com/Reedtrullz/Bunkerkartet/issues/13), [BK-05 / #17](https://github.com/Reedtrullz/Bunkerkartet/issues/17), [BK-06 / #18](https://github.com/Reedtrullz/Bunkerkartet/issues/18). **Files:** `scripts/benchmark_catalogue.py (new)`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_catalogue_queries.py (new)`.

**Contract:** Conditional performance work consumes stable search/read contracts; no shared/private response cache.

- [ ] **Regressions — Test BK-29:** Before/after same fixtures preserve 500-record effects and auth/filter reset; list observation queries bounded; if impact negligible, deliver reproducible probe and disposition instead of speculative indexing.
- [ ] **Implement:** Record SQL counts, payload size, time/memory/render at fixed synthetic sizes; declare improvement target before edits. Batch proven N+1 queries and avoid full refresh/preview repetition only where measured.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Publish before/after measurements on fixed fixtures with a declared target before coding; observation retrieval is bounded per list rather than per site; 500-record import effects remain identical; map/filter/auth resets remain correct. If measurements show no worthwhile user impact, keep the probe and skip speculative optimization.

<a id="bk-45-issue-57"></a>
#### BK-45 / [issue #57](https://github.com/Reedtrullz/Bunkerkartet/issues/57) — Stage selected import records without losing package provenance

**Priority:** P3. **Required predecessors:** [BK-36 / #48](https://github.com/Reedtrullz/Bunkerkartet/issues/48), [BK-44 / #56](https://github.com/Reedtrullz/Bunkerkartet/issues/56). **Files:** `app/main.py`, `app/imports.py`, `app/static/app.js`. **Tests:** `tests/test_import_staging.py (new)`, `tests/test_import_api.py`.

**Contract:** Subset is a new package identity with parent provenance, never partial writes under original batch.

- [ ] **Regressions — Test BK-45:** Preview invalidated by selection edit; submitted/downloaded keys match; unselected referenced keys unresolved; independent subsets and original receipt traceable; atomic commit unchanged.
- [ ] **Implement:** Create explicit subset-derived package with new batch ID, parent hash, selected stable keys and reason; always fresh preview for exact selected payload. Deferred/unselected remain staging choices.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Downloaded/submitted subset matches selected keys and its own receipt; changing selection invalidates preview; referenced unselected keys remain unresolved rather than silently imported; original package/hash and later independent selections remain auditable.

<a id="bk-47-issue-59"></a>
#### BK-47 / [issue #59](https://github.com/Reedtrullz/Bunkerkartet/issues/59) — Represent contradicting and dependent research claims

**Priority:** P3. **Required predecessors:** [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25), [BK-15 / #27](https://github.com/Reedtrullz/Bunkerkartet/issues/27), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_claim_assertions.py (new)`.

**Contract:** Private claim assertion links consume stable per-site IDs, richer evidence and revisioned curator queue.

- [ ] **Regressions — Test BK-47:** Contradictions coexist; derived readings not counted independent; reviewed refs/reason saved; reopened question never changes approach/trust automatically.
- [ ] **Implement:** Pilot bounded supports/contradicts/derived-from links with lineage and reasoned disposition; preserve context/time differences and use a table/list first.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Contradictory claims coexist and are individually attributable; derived citations do not inflate independence; resolving a conflict records the reviewed references/reason; reopening a decision never silently changes status or approach permission.

<a id="bk-48-issue-60"></a>
#### BK-48 / [issue #60](https://github.com/Reedtrullz/Bunkerkartet/issues/60) — Compare competing identity hypotheses without merging them

**Priority:** P3. **Required predecessors:** [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_identity_hypotheses.py (new)`.

**Contract:** Hypothesis belongs to question/revision; original marker remains until separate protected decision.

- [ ] **Regressions — Test BK-48:** Several hypotheses unresolved together; candidate/current point labeled; resolution never bypasses merge/location review; private discussion excluded from ordinary exports.
- [ ] **Implement:** Attach bounded hypotheses and source/claim references to identity question with original wording, objections and optional source-derived point; compare and explicitly retain/reject/defer.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Several hypotheses can remain unresolved; comparison labels candidate versus current point; resolving a hypothesis does not bypass merge/location review; private discussion is absent from ordinary exports/public source.

<a id="bk-51-issue-63"></a>
#### BK-51 / [issue #63](https://github.com/Reedtrullz/Bunkerkartet/issues/63) — Review controlled GIS edits through a revision-bound round trip

**Priority:** P3. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-18 / #30](https://github.com/Reedtrullz/Bunkerkartet/issues/30), [BK-50 / #62](https://github.com/Reedtrullz/Bunkerkartet/issues/62). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_gis_roundtrip.py (new)`.

**Contract:** GIS pack carries source revision and semantic role; only feature geometry edits allowed, not approach/trust.

- [ ] **Regressions — Test BK-51:** Unchanged rows no write; stale revision, detected axis violations/out-of-range and role changes reject; valid edit sets location-review like native edits; no guessing ambiguous valid coordinate pairs.
- [ ] **Implement:** Export selected edit pack with stable keys/revisions/roles and editable allowlist; preview returned WGS84 geometry side by side, require reason and commit through native location/revision guard.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Synthetic export-edit-preview-commit round trips exactly; stale revisions, swapped/out-of-range coordinates and role changes fail clearly; unchanged rows cause no writes; reviewed coordinate changes trigger the same location-review state as native edits.

<a id="bk-53-issue-65"></a>
#### BK-53 / [issue #65](https://github.com/Reedtrullz/Bunkerkartet/issues/65) — Group planned questions and observations into private visits

**Priority:** P3. **Required predecessors:** [BK-08 / #20](https://github.com/Reedtrullz/Bunkerkartet/issues/20), [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25), [BK-19 / #31](https://github.com/Reedtrullz/Bunkerkartet/issues/31). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_visits.py (new)`.

**Contract:** Visit separates itinerary intention from occurred evidence; membership consumes immutable route and observation identities.

- [ ] **Regressions — Test BK-53:** Plan cannot imply actual observation; skipped question stays open; same observation attach retry deduplicates; no route provider needed; private revisions/retention covered.
- [ ] **Implement:** Add private visit record for route snapshot or manual eligible approaches, chosen questions, planned/occurred dates and outcomes; attach observations idempotently without rewriting identity.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** One observation can be attributed to a visit without changing its original identity; skipped questions remain open; future plans cannot masquerade as completed observations; session membership/revisions/retention are private and explicit.

<a id="bk-54-issue-66"></a>
#### BK-54 / [issue #66](https://github.com/Reedtrullz/Bunkerkartet/issues/66) — Record the limits of negative field observations

**Priority:** P3. **Required predecessors:** [BK-19 / #31](https://github.com/Reedtrullz/Bunkerkartet/issues/31), [BK-53 / #65](https://github.com/Reedtrullz/Bunkerkartet/issues/65). **Files:** `app/main.py`, `app/db.py`, `app/static/app.js`. **Tests:** `tests/test_visits.py (new)`, `tests/test_observation_amendments.py (new)`.

**Contract:** Negative observation is bounded evidence, never proof of absence or movement trail.

- [ ] **Regressions — Test BK-54:** Not-found/inaccessible render scope/limits; legacy notes receive no inferred details; amendment original intact; repeated negative outcomes do not auto-reject site.
- [ ] **Implement:** Add optional negative-observation sought target, public viewpoint/area description, visibility limitations and unknown coverage; preserve dated limits and link unanswered question.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Not-found/inaccessible outcomes render their scope and limits; no extra information is guessed for legacy notes; amendments retain original context; lifecycle decisions require independent explicit review even after repeated negative observations.

<a id="bk-55-issue-67"></a>
#### BK-55 / [issue #67](https://github.com/Reedtrullz/Bunkerkartet/issues/67) — Support deliberate route endpoints and return-to-start plans

**Priority:** P3. **Required predecessors:** [BK-07 / #19](https://github.com/Reedtrullz/Bunkerkartet/issues/19), [BK-09 / #21](https://github.com/Reedtrullz/Bunkerkartet/issues/21), [BK-50 / #62](https://github.com/Reedtrullz/Bunkerkartet/issues/62). **Files:** `app/routes.py`, `app/main.py`, `app/db.py`, `app/static/app.js`. **Tests:** `tests/test_routes.py`, `tests/test_route_api.py`, `tests/test_browser_smoke.py`.

**Contract:** Endpoints are routing inputs, not catalogue identities; mode/end included in semantic hash.

- [ ] **Regressions — Test BK-55:** Changing end invalidates old result; start=end not duplicate site; provider and GPX ordered endpoints agree; intermediate approach guards remain; old one-way readable.
- [ ] **Implement:** Extend immutable draft with one-way/explicit-end/return-to-start mode and non-site endpoint fields; include in request identity/provider waypoints/snapshot/privacy preview/GPX.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Start/end/order are bound to one draft and saved route; identical start/end does not become a duplicated catalogue stop; GPX and provider waypoints agree; changing endpoint invalidates results; legacy one-way routes remain readable.

<a id="bk-56-issue-68"></a>
#### BK-56 / [issue #68](https://github.com/Reedtrullz/Bunkerkartet/issues/68) — Show per-leg travel estimates and explicit visit-time budgets

**Priority:** P3. **Required predecessors:** [BK-09 / #21](https://github.com/Reedtrullz/Bunkerkartet/issues/21), [BK-53 / #65](https://github.com/Reedtrullz/Bunkerkartet/issues/65), [BK-55 / #67](https://github.com/Reedtrullz/Bunkerkartet/issues/67). **Files:** `app/routes.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_routes.py`, `tests/test_visits.py (new)`.

**Contract:** Travel estimate differs from planned visit duration and actual dwell; no deadline or accessibility guarantee.

- [ ] **Regressions — Test BK-56:** Leg count/order/totals reconcile to provider contract; missing legs not divided from total; manual budget edits make no provider call and cannot relabel old geometry fresh.
- [ ] **Implement:** Retain validated official provider segment metrics and separately entered planned visit times; show itinerary/budget total with unavailable states, bound to route draft/snapshot.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Leg totals reconcile within documented provider semantics; missing segments remain unavailable; travel versus visit time is distinct; budget changes do not make old geometry appear newly computed; no hidden provider request occurs during manual edits.

<a id="bk-57-issue-69"></a>
#### BK-57 / [issue #69](https://github.com/Reedtrullz/Bunkerkartet/issues/69) — Retain route calculation provenance and approved profile semantics

**Priority:** P3. **Required predecessors:** [BK-07 / #19](https://github.com/Reedtrullz/Bunkerkartet/issues/19), [BK-09 / #21](https://github.com/Reedtrullz/Bunkerkartet/issues/21), [BK-55 / #67](https://github.com/Reedtrullz/Bunkerkartet/issues/67). **Files:** `app/routes.py`, `app/main.py`, `app/db.py`, `app/static/app.js`. **Tests:** `tests/test_route_api.py`, `tests/test_routes.py`.

**Contract:** Hiking remains default; profile choice independent from access/safety and requires acceptance.

- [ ] **Regressions — Test BK-57:** Old routes show unknown provenance; config/profile change invalidates results; safe hashes correlate inputs without logged coordinates; unadvertised metadata stays unknown.
- [ ] **Implement:** Store minimal calculation receipt with provider/profile/time/request semantic hash/normalizer version and optional safe engine metadata; prepare allowlisted profile UI only after owner choice.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A saved route explains its configuration and unknown provider-version fields; profile changes invalidate draft results; identical inputs remain identifiable without exposing private coordinates in logs; legacy routes are labeled as missing calculation provenance.

<a id="bk-58-issue-70"></a>
#### BK-58 / [issue #70](https://github.com/Reedtrullz/Bunkerkartet/issues/70) — Capture opt-in observation fixes without inventing feature accuracy

**Priority:** P3. **Required predecessors:** [BK-03 / #15](https://github.com/Reedtrullz/Bunkerkartet/issues/15), [BK-18 / #30](https://github.com/Reedtrullz/Bunkerkartet/issues/30), [BK-20 / #32](https://github.com/Reedtrullz/Bunkerkartet/issues/32). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_browser_smoke.py`, `tests/test_api.py`.

**Contract:** Device fix is an observation measurement only; no watchPosition, background trail or automatic adoption.

- [ ] **Regressions — Test BK-58:** Permission failure or stale fix leaves usable draft; old callbacks ignored; no save until submit; GPS accuracy never becomes feature radius or adopted point.
- [ ] **Implement:** Add explicit one-shot geolocation into observation draft with timestamp/age/instrument accuracy distinct from feature uncertainty; retain manual input and epoch/draft cancellation.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Permission failure/stale fix leaves the draft usable; epoch/draft generation blocks obsolete callbacks; instrument accuracy and adopted feature radius remain different fields; no position is saved before an explicit observation submission.

<a id="bk-59-issue-71"></a>
#### BK-59 / [issue #71](https://github.com/Reedtrullz/Bunkerkartet/issues/71) — Exchange complete private workspaces through a versioned manifest

**Priority:** P3. **Required predecessors:** [BK-25 / #37](https://github.com/Reedtrullz/Bunkerkartet/issues/37), [BK-26 / #38](https://github.com/Reedtrullz/Bunkerkartet/issues/38), [BK-34 / #46](https://github.com/Reedtrullz/Bunkerkartet/issues/46), [BK-50 / #62](https://github.com/Reedtrullz/Bunkerkartet/issues/62). **Files:** `scripts/export_workspace.py (new)`, `scripts/import_workspace.py (new)`, `app/main.py`. **Tests:** `tests/test_workspace_exchange.py (new)`, `tests/test_restore_archive.py`.

**Contract:** Manifest exchange differs from stopped-writer operational backup; no .env/token inclusion or automatic live merge.

- [ ] **Regressions — Test BK-59:** Full synthetic exchange reconstructs content/history; missing/tampered/wrong-version/path-unsafe members fail before acceptance; source workspace unchanged; exclusions listed.
- [ ] **Implement:** Define bounded versioned exchange archive with SHA/schema/seed IDs/counts/checksums, original seed/effective overrides and selected provenance/private records; stage into separate workspace before approval.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** A synthetic full export reconstructs equivalent effective content/history in an isolated compatible app; missing/wrong-version/tampered members fail before acceptance; conflicts are previewed; the original workspace is untouched and exclusions are visible.

<a id="bk-61-issue-73"></a>
#### BK-61 / [issue #73](https://github.com/Reedtrullz/Bunkerkartet/issues/73) — Replay seeded action sequences against trust and provenance invariants

**Priority:** P2. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-04 / #16](https://github.com/Reedtrullz/Bunkerkartet/issues/16), [BK-14 / #26](https://github.com/Reedtrullz/Bunkerkartet/issues/26), [BK-19 / #31](https://github.com/Reedtrullz/Bunkerkartet/issues/31), [BK-38 / #50](https://github.com/Reedtrullz/Bunkerkartet/issues/50). **Files:** `tests/test_action_sequences.py (new)`, `tests/fixtures/sequences/ (new)`. **Tests:** `tests/test_action_sequences.py (new)`.

**Contract:** Independent invariants, not implementation-derived expected values; synthetic suite does not prove historical truth.

- [ ] **Regressions — Test BK-61:** Fixed seeds replay; injected candidate promotion/hash rewrite/evidence duplication guards fail oracle; bounded CI runtime measured; reduced trace ordinary regression.
- [ ] **Implement:** Build bounded stdlib seeded action sequences with independent oracle for imports/reviews/edits/amendments/merges/retries/migrations/routes; save seed/actions and reduce failures to regressions.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Fixed seeds replay identically; injected guard/hash/evidence regressions are detected; shortened failing traces become ordinary tests; a declared bounded CI runtime is measured before expanding the corpus.

<a id="bk-62-issue-74"></a>
#### BK-62 / [issue #74](https://github.com/Reedtrullz/Bunkerkartet/issues/74) — Show provenance-qualified catalogue quality and review coverage

**Priority:** P3. **Required predecessors:** [BK-08 / #20](https://github.com/Reedtrullz/Bunkerkartet/issues/20), [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25), [BK-16 / #28](https://github.com/Reedtrullz/Bunkerkartet/issues/28), [BK-35 / #47](https://github.com/Reedtrullz/Bunkerkartet/issues/47). **Files:** `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_quality_coverage.py (new)`.

**Contract:** Coverage state is dimensional and evidence-qualified, not map color or visitor analytics.

- [ ] **Regressions — Test BK-62:** Fixture counts only own dimension; unknown/overdue separate; drill-down matches membership; no universal truth/safety score or auto-promotion.
- [ ] **Implement:** Add private read-only counts/drill-down for independent identity/location/access/currentness/evidence dimensions, with explicit total/filtered denominators and unresolved question/source-dependence views.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Fixtures prove each dimension counts only its own evidence; unknown/overdue values remain distinct; filtered and total denominators are stated; drill-down membership matches counts; no dashboard action changes trust without explicit review.

### Wave 6 — Explicitly gated product pilots

<a id="bk-30-issue-42"></a>
#### BK-30 / [issue #42](https://github.com/Reedtrullz/Bunkerkartet/issues/42) — Offer an opt-in offline field notebook

**Priority:** P4. **Required predecessors:** [BK-20 / #32](https://github.com/Reedtrullz/Bunkerkartet/issues/32), [BK-21 / #33](https://github.com/Reedtrullz/Bunkerkartet/issues/33), [BK-24 / #36](https://github.com/Reedtrullz/Bunkerkartet/issues/36), [BK-26 / #38](https://github.com/Reedtrullz/Bunkerkartet/issues/38). **Files:** `app/static/offline-notebook.js (new)`, `app/static/app.js`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_offline_notebook.py (new)`, `tests/test_browser_smoke.py`.

**Contract:** Opt-in persistent device pack is separate from memory drafts; no bearer caching or tile prefetch.

- [ ] **Regressions — Test BK-30:** Offline interruption/retry produces one observation; changed sites reviewed before sync; stale pack labeled; erase removes approved local data; session lock versus approved storage exposure explicit.
- [ ] **Implement:** After device/privacy policy approval, pilot explicit selected text/GPX pack and bounded expiring observation outbox with original request IDs and manual per-item sync review.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Offline/online interruption produces no duplicate observations; changed server sites are reviewed before sync; stale packs are labeled; clearing an approved local pack removes its data/outbox; tests distinguish persisted device exposure from ordinary lock privacy. Tile caching requires a separate provider-terms decision.

<a id="bk-31-issue-43"></a>
#### BK-31 / [issue #43](https://github.com/Reedtrullz/Bunkerkartet/issues/43) — Explore historically sourced site relationships and time layers

**Priority:** P4. **Required predecessors:** [BK-13 / #25](https://github.com/Reedtrullz/Bunkerkartet/issues/25), [BK-14 / #26](https://github.com/Reedtrullz/Bunkerkartet/issues/26), [BK-18 / #30](https://github.com/Reedtrullz/Bunkerkartet/issues/30). **Files:** `app/db.py`, `app/main.py`, `app/static/app.js`. **Tests:** `tests/test_historical_relations.py (new)`.

**Contract:** Typed historical assertions require source, certainty, date context and curator disposition; optional footprints need actual evidence.

- [ ] **Regressions — Test BK-31:** Legacy generic links survive; contradictory/uncertain edges coexist; no trust/access changes; reviewed sample demonstrates useful navigation.
- [ ] **Implement:** Pilot source-backed typed relations/date intervals on a small owner-selected sample; keep uncertain/generic links and current approaches separate; use a small list/time-layer explorer before graph tooling.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Uncertain edges are visually distinct and retain provenance; contradictory sources can coexist; typed assertions never change site trust/access; legacy relations remain readable; an independently reviewed sample demonstrates useful navigation before wider adoption.

<a id="bk-32-issue-44"></a>
#### BK-32 / [issue #44](https://github.com/Reedtrullz/Bunkerkartet/issues/44) — Support private readers and separately approved publication packages

**Priority:** P4. **Required predecessors:** [BK-02 / #14](https://github.com/Reedtrullz/Bunkerkartet/issues/14), [BK-24 / #36](https://github.com/Reedtrullz/Bunkerkartet/issues/36), [BK-26 / #38](https://github.com/Reedtrullz/Bunkerkartet/issues/38), [BK-28 / #40](https://github.com/Reedtrullz/Bunkerkartet/issues/40). **Files:** `app/config.py`, `app/main.py`, `scripts/build_publication_package.py (new)`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_reader_auth.py (new)`, `tests/test_publication_package.py (new)`.

**Contract:** Read-only scope does not grant publication; separate credential and export approval decisions.

- [ ] **Regressions — Test BK-32:** Reader fails every mutation and revokes; canaries exclude queues/observations/starts/raw payload; each output record has explicit rights/coordinate/selection decision and matches preview.
- [ ] **Implement:** Split into scoped read-only credential/revocation and later selected static publication generator with field/rights/coordinate approval receipt. Prepare both locally; publication host remains separately authorized.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Read-only credentials fail every mutation path and expire/revoke predictably; export canaries prove exclusion of private data; every published record has an explicit selection/rights/coordinate decision; package membership matches signed-off preview. Field trust never means publication approval.

<a id="bk-63-issue-75"></a>
#### BK-63 / [issue #75](https://github.com/Reedtrullz/Bunkerkartet/issues/75) — Generalize geographic workspace policy without multi-tenant infrastructure

**Priority:** P4. **Required predecessors:** [BK-23 / #35](https://github.com/Reedtrullz/Bunkerkartet/issues/35), [BK-42 / #54](https://github.com/Reedtrullz/Bunkerkartet/issues/54), [BK-59 / #71](https://github.com/Reedtrullz/Bunkerkartet/issues/71). **Files:** `app/config.py`, `app/imports.py`, `app/static/app.js`, `docs/CURATION_POLICY.md`. **Tests:** `tests/test_workspace_policy.py (new)`.

**Contract:** One owner-selected descriptor, no multi-tenant/account platform; geography never inferred from GPS.

- [ ] **Regressions — Test BK-63:** Two synthetic descriptors consistently update defaults; namespace keys collision-safe; out-of-scope import reviewed rather than coerced; Trondheim records unchanged.
- [ ] **Implement:** Pilot single-owner workspace descriptor for name/view/start defaults/approved source namespaces/regional scope; preserve one SQLite instance and explicit out-of-scope review.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Two synthetic descriptors change all intended defaults consistently; keys remain collision-safe across approved sources; imports outside selected scope receive explicit review rather than silent coordinate coercion; legacy Trondheim data remains unchanged.

<a id="bk-64-issue-76"></a>
#### BK-64 / [issue #76](https://github.com/Reedtrullz/Bunkerkartet/issues/76) — Pilot licensed historical raster comparison with calibration evidence

**Priority:** P4. **Required predecessors:** [BK-18 / #30](https://github.com/Reedtrullz/Bunkerkartet/issues/30), [BK-31 / #43](https://github.com/Reedtrullz/Bunkerkartet/issues/43), [BK-43 / #55](https://github.com/Reedtrullz/Bunkerkartet/issues/55), [BK-49 / #61](https://github.com/Reedtrullz/Bunkerkartet/issues/61). **Files:** `app/static/app.js`, `app/static/historical-raster.js (new)`, `docs/HISTORICAL_RASTER_PILOT.md (new)`. **Tests:** `tests/test_raster_pilot.py (new)`, `tests/test_browser_smoke.py`.

**Contract:** Historical calibration is independent of present approach coordinates/access; no unlicensed image in Git.

- [ ] **Regressions — Test BK-64:** Independent sample verifies rights/calibration limits; removing control point updates receipt; missing raster leaves workspace usable; annotations/trust never auto-derived.
- [ ] **Implement:** Use one licensed owner-approved raster and established georeferencing tooling; retain archive/rights/date/control points/transform/residual receipt and opacity/swipe overlay with uncertainty.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** An independently reviewed sample documents rights and calibration limits; control-point removal changes the receipt predictably; unavailable imagery leaves normal research usable; map annotations retain their own evidence/radius and are never auto-promoted from pixel alignment.

<a id="bk-65-issue-77"></a>
#### BK-65 / [issue #77](https://github.com/Reedtrullz/Bunkerkartet/issues/77) — Pilot privately owned photo evidence with bounded storage controls

**Priority:** P4. **Required predecessors:** [BK-19 / #31](https://github.com/Reedtrullz/Bunkerkartet/issues/31), [BK-26 / #38](https://github.com/Reedtrullz/Bunkerkartet/issues/38), [BK-42 / #54](https://github.com/Reedtrullz/Bunkerkartet/issues/54), [BK-49 / #61](https://github.com/Reedtrullz/Bunkerkartet/issues/61). **Files:** `app/main.py`, `app/db.py`, `app/attachments.py (new)`, `scripts/backup_database.py (new)`, `scripts/restore_database.py (new)`. **Tests:** `tests/test_attachments.py (new)`, `tests/test_restore_procedure.py (new)`.

**Contract:** Attachments enabled only after backup/restore qualification and explicit metadata/storage policy; no remote URL fetch or SVG/HTML.

- [ ] **Regressions — Test BK-65:** Spoofed type, huge/decompression-heavy fixtures bounded; auth enforced; EXIF canaries checked; original/derived retention stated; restore/delete covers files/thumbnails/refs.
- [ ] **Implement:** After rights/threat-model decision, add owned-image authenticated upload using established safe decoder/re-encoder, strict bytes/type/dimensions/pixel limits, metadata policy/checksum/reference and backup/retention manifests.
- [ ] **Verify and deliver:** Run the focused tests above and the applicable shared checks; commit a focused change, attach exact-head evidence, then assess every original acceptance requirement below. Preserve scope boundaries from the frozen spec.

**Acceptance:** Spoofed/oversized/decompression-heavy fixtures fail boundedly; unauthenticated access is blocked; metadata policy is verified against canaries; original-versus-derived retention is explicit; restore and approved deletion include bytes/thumbnails/references without orphaning provenance.

## Owner and manual gates: concrete decisions, not hidden dependencies

Prepare a decision packet with default-safe implementation, synthetic proof and an exact proposed configuration/output before requesting activation. These decisions do not block independent correctness work.

| Items | Required decision or independent acceptance | Result to prepare |
|---|---|---|
| #1; BK-28 / #40 | Approved runtime lane; required CI/check names, bypass/force-push approvers | Recommend retaining 3.12; exact GitHub rule/update config and compatibility receipts. |
| BK-16/17 / #28/#29 | Approach freshness intervals and valid lifecycle/public-viewpoint review criteria | Date-controlled examples and named evidence/reason workflow. |
| BK-26 / #38 | RPO/RTO, retention, offsite destination/ownership, deletion selection | Policy config, exact deletion preview and synthetic restore receipts; verified encryption/transfer before real archive use. |
| BK-22 / #34 | Manual screen-reader/mobile acceptance | Keyboard/error/focus checklist and browser evidence; manual/device result separately recorded. |
| BK-42/43 / #54/#55 | Lock intervals, real rotation boundary, external basemap/provider choice | Synthetic lock/rotation/network proof and reviewable operational procedure; no external traffic in disabled mode. |
| BK-57 / #69 | Walking-profile choice; current official provider semantics | Receipt schema and fixture proof; preserve hiking default until selected. |
| BK-30 / #42 | Offline device/privacy/erase policy | Small text/GPX/outbox pilot and measurable exposure/sync/erase evidence. |
| BK-31 / #43 | Historical relation sample and independently reviewed usefulness | Small attributed sample, legacy comparison and uncertainty display. |
| BK-32 / #44 | Reader scope/revocation; separate publication rights/coordinates/membership and hosting approval | Read-only credential proof plus exact static package preview and exclusion canaries. |
| BK-63 / #75 | Actual geographic scope/workspace descriptors | Two synthetic descriptors and review examples; no nationwide/tenant rollout. |
| BK-64 / #76 | Licensed image, independent calibration and chosen tooling | Rights record/control points/residual receipt and one reviewed sample. |
| BK-65 / #77 | Image rights/threat model, metadata policy, byte/pixel/storage/retention budgets | Reviewable upload/backup/restore pilot with bounded adversarial fixtures before activation. |

Each conditional issue has a technical path above. If the owner declines a pilot, record an explicit declined/not-planned disposition; do not quietly mark an unimplemented acceptance target completed. If a feature ships behind a gate, distinguish merged engineering from accepted product behavior.

## Completion ledger and closure rule

Execution was authorized after this frozen planning snapshot. See [execution results](2026-10-03-bunkerkartet-execution-results.md) and the current ledger for implemented scope, exact verification and remaining acceptance gates; the original task checklists below are preserved as planning requirements.

The [initialized planning ledger](2026-10-03-bunkerkartet-all-open-items-ledger.json) contains all 65 issue tasks, five PR dispositions and the dependency-safe wave order; the original entries began planned. During execution, extend each item with branch/PR, base/head SHA, test/CI receipt, acceptance evidence, owner/manual gate, schema/rollback compatibility, final disposition and readback URL.

Status vocabulary: planned → implementing → locally verified → PR verified → integrated → owner/manual acceptance pending → accepted. PRs may instead resolve as superseded or policy-declined with an explicit rationale. “Merged” alone is not “accepted”; “healthy” is not “deployed requested behavior.”

Before reporting all work complete:

- [ ] Re-enumerate GitHub and reconcile every initial 65 issue/five-PR identity plus any accepted scope changes.
- [ ] Check every declared dependency and original acceptance target; no undocumented blocker or skipped gate.
- [ ] Run integrated unit/API/migration/restore/browser/frontend/build and synthetic non-root readiness checks after final code changes.
- [ ] Verify current-head CI and read back every authorized merge/closure/disposition.
- [ ] Preserve separate owner/manual/source/rights/device/privacy acceptance results; disclose remaining gates instead of closing early.
- [ ] If deployment is authorized, record live exact-version readiness, backup/recovery coordinates and requested authenticated user-flow proof.
- [ ] Log final evidence/non-claims in Obsidian and clean only run-owned inactive scratch artifacts.

## Planning verification receipt

The plan has one implementation card for each BK-01–BK-65 and one resolution entry for each open PR #1/#7/#9/#11/#12. All **116 explicit required dependency edges** are covered and the listed execution order is topologically valid. Phase-0 diagnostics may begin before their downstream feature tests are ready; in particular BK-11 reproduction/artifact work starts immediately, while its BK-06/07 flow acceptance waits for those changes.

Planning evidence is GitHub inventory/PR diffs/checks/failing logs, pinned-main source inspection, local ref/state inventory and static coverage/dependency validation. No implementation, new application test run, branch switch, private database access, GitHub mutation or production action was performed.
