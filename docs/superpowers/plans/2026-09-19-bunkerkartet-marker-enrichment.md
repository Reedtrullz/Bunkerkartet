# Bunkerkartet marker enrichment and research-state rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the completed research pass for all 85 Bunkerkartet markers into truthful, source-backed enrichment in staged waves, while preserving canonical database facts, candidate status, coordinates, identity uncertainty, and access safety.

**Architecture:** Keep the existing additive `site_enrichment.json` overlay. Add a small explicit research-state field to overlay records and a truthful fallback state for records that have no curated overlay. Keep the 85-marker research report as the separate curator coverage matrix; its “baseline only/no robust external source” status must not be conflated with the public map’s “not curated” presentation state. Do not make the app scrape sources, infer access from photos/trails, merge possible duplicates, or import every researched row automatically.

**Tech Stack:** FastAPI/Pydantic, JSON content overlay, vanilla JavaScript frontend, SQLite-backed API, pytest, existing browser smoke tests, and the existing research report/curation policy. No new runtime dependencies.

**Spec:** `research/marker-research-2026-09-19.md` and `docs/CURATION_POLICY.md`, extended by the user’s request to enrich all 85 markers from verified online research.

- Every map-visible overlay record must be distinguishable between “not curated in the map”, “research found but awaiting curation”, “identity requires review”, and “source-backed content curated”. The local 85-marker coverage matrix must separately distinguish “baseline only/no robust external source” from “external source found”.
- Empty enrichment sections must not claim that no online information exists.
- The first content wave covers only high-confidence, identifiable places: Kuhaugen, Polsmohulen, Østmarkneset, Værvarslingstasjon, Nordblitz, Persaunet, Vollan, Misjonshotellet, and Kystadhaugen, in addition to the existing local enrichment records including Leira batteri.
- The possible duplicate/identity cases remain an explicit review queue. No automatic merge, coordinate move, status promotion, or access permission is allowed.
- Public approach, physical access to the object, permission to enter, and safety restrictions remain separate claims.
- Research and overlay changes stop at local review/preview. Production import, push, PR, merge, and deploy require a later explicit instruction.

## Global Constraints

- Preserve the existing canonical site facts and candidate status; enrichment is additive and must never rewrite the database record.
- Use only claims that have a direct source link in the overlay. Omit unsupported fields or mark them `unknown`; never fill gaps with plausible prose.
- Keep source URLs, source access dates, and enrichment review dates visible enough for a curator to judge freshness.
- Apply this source hierarchy: official/archival sources for identity, ownership, and access; specialist/local-history sources for structure and wartime history; forums, trip reports, Peakbook, and geocaching for observed approach or visible remains only; search-result snippets for discovery only, never as evidence.
- Treat private land, military areas, dangerous structures, underwater sites, and permission-sensitive locations conservatively. Do not add precise entry directions.
- Do not add a scraper, background worker, LLM call, new database table, or new frontend framework.
- Keep the existing public-data policy intact: site-by-site research decisions remain in the local curator material until explicitly approved for publication.

## Review Focus

- Schema compatibility: old overlay records must continue to load without a migration.
- Truthfulness: “not curated” must not be rendered as “not researched”; source-supported, uncertain, and unknown claims must remain visually distinct.
- Identity: the five known duplicate/identity families must be reviewed manually and never collapsed by key similarity.
- Safety: public approach must not be presented as permission to enter an object or private/military land.
- Regression surface: API detail and summary responses, search, frontend fallback rendering, browser smoke flows, and the existing full test suite.

---

## Task 1: Freeze the research manifest and the first rollout boundary

**Files:**

- Modify `research/marker-research-2026-09-19.md` only to add the compact coverage-status legend.
- Read `docs/CURATION_POLICY.md`.
- Do not modify the canonical database, import endpoints, or live API.

- [ ] Verify that the report contains exactly 85 live external keys with no missing or extra marker relative to the read-only research snapshot.
- [ ] Treat the report’s per-marker source inventory and A/N/F/Ad assessments as the working evidence base; do not create a second catalog that duplicates the report.
- [ ] Add a compact status legend to the report: `baseline_only`, `external_found`, `identity_review`, and `curation_ready`, with `baseline_only` reserved for markers where no robust external source was confirmed.
- [ ] Freeze the first content-wave allowlist to `krigskart:2663`, `krigskart:2673`, `krigskart:2676`, `krigskart:2677`, `krigskart:3475`, `krigskart:721`, `krigskart:742`, `krigskart:3479`, and `kystfort:topic:414`, plus the already curated overlay records.
- [ ] Record the rollout boundary in the curator notes: research coverage is 85/85, but map-visible enrichment is intentionally staged and smaller.
- [ ] Run this read-only key-count check against the report before any content edit; fail the task if the count is not 85/85:

  ```bash
  test "$(awk -F'|' '/^\\| (krigskart|tracesofwar|kystfort|openstreetmap):/{n++} END{print n+0}' research/marker-research-2026-09-19.md)" -eq 85
  rg -n 'baseline_only|external_found|identity_review|curation_ready' research/marker-research-2026-09-19.md
  ```

**Acceptance:** The implementation has a fixed nine-key first wave, an explicit 85/85 research-coverage statement, and no implied authorization to import or deploy.

**Checkpoint:** No repository commit is required for the read-only boundary check; continue only after the 85/85 assertion and status legend are recorded.

## Task 2: Add truthful research states and source freshness to the overlay/UI

**Files:**

- Modify `app/enrichment.py`.
- Modify `app/main.py`.
- Modify `app/static/app.js`.
- Modify `tests/test_enrichment.py`.
- Modify `tests/test_api.py`.
- Modify `tests/test_frontend_contract.py`.
- Modify `tests/test_browser_smoke.py`.
- Modify `docs/CURATION_POLICY.md` with the staged-enrichment state definitions.

- [ ] Add a backward-compatible `research_state` literal to `ResearchSite` with exactly `curated`, `researched_pending`, and `identity_review`; default legacy overlay records to `curated` so schema version 1 remains loadable.
- [ ] Return `research_state` from `load_site_enrichment()` and include it in the API site summary whenever an overlay exists; leave canonical site fields untouched.
- [ ] Define the missing-overlay fallback as `not_curated` inside the presentation layer, not as a stored claim about the state of online research.
- [ ] Render these labels in the detail panel: `Kildeunderlag kuratert`, `Research funnet – venter på kuratering`, `Identitet må avklares`, and `Ikke kuratert i kartet`.
- [ ] Replace the empty-section text `Ikke beriket i kildeunderlaget.` with wording that says only that no source-backed claim has been curated for that field.
- [ ] Render each source’s `accessed_at` date alongside its link and keep the site-level `reviewed_at` date visible; do not imply that a date guarantees current access conditions.
- [ ] Add API and frontend tests proving that an un-enriched site shows the not-curated state, a pending/identity-review overlay preserves its state, and an enriched detail still exposes source links and canonical facts.
- [ ] Add a browser smoke assertion that the new status text appears and the old misleading fallback text does not appear.
- [ ] Document that the public map’s `not_curated` label is a presentation state, not a statement that research was not performed; use the report’s separate status legend for curator coverage.
- [ ] Document the source hierarchy from the global constraints and require access claims to come from an owner/authority source or be labeled as a dated observation rather than permission.
- [ ] Run the focused test cycle:

  ```bash
  python3 -m pytest -q tests/test_enrichment.py tests/test_api.py tests/test_frontend_contract.py
  python3 -m pytest -q tests/test_browser_smoke.py -k 'site_detail or enrichment'
  node --check app/static/app.js
  ```

**Acceptance:** A user can tell the difference between missing curation, pending research, unresolved identity, and curated content without being told that the absence of a local claim means the internet has no information.

**Commit:** `feat: expose truthful enrichment research states`

## Task 3: Curate the first nine high-confidence content entries

**Files:**

- Modify `app/content/site_enrichment.json`.
- Modify `tests/test_enrichment.py`.
- Modify `tests/test_api.py`.
- Modify `tests/test_browser_smoke.py` only where a new high-confidence detail fixture needs coverage.

- [ ] Add one overlay record for each first-wave key: `krigskart:2663`, `krigskart:2673`, `krigskart:2676`, `krigskart:2677`, `krigskart:3475`, `krigskart:721`, `krigskart:742`, `krigskart:3479`, and `kystfort:topic:414`.
- [ ] Add an explicit `research_state: curated` to all existing overlay records, including `krigskart:2666`, so no legacy default silently upgrades a future record to curated.
- [ ] Set every first-wave record to `research_state: curated` only after its identity is unambiguous in the source material; keep an entry out of the wave if the source evidence contradicts the marker identity.
- [ ] Add only claims that can be traced through `source_ids` to direct source URLs. Prioritize “Om stedet” and “Hva finnes her i dag”; add physical-access or access-rule text only when the source explicitly supports it.
- [ ] Use at least two independent sources for a site’s core historical description when the report provides them; when only one suitable source exists, keep the claim narrow and use `uncertain` where the source does not establish the point conclusively.
- [ ] Separate public approach/trail observations from entry permission, object accessibility, private-land restrictions, military restrictions, and danger warnings.
- [ ] Preserve the existing `krigskart:2666` Leira entry and its conservative access wording; do not replace its local overlay with a database edit.
- [ ] Validate that every claim’s `source_ids` resolves within the same record and that every URL is absolute HTTPS before running the application.
- [ ] Assert the expected overlay key set in the enrichment tests: the existing 11 local records plus these nine new keys, with no accidental extra record.
- [ ] Run the focused content cycle:

  ```bash
  python3 -m pytest -q tests/test_enrichment.py tests/test_api.py
  python3 -m pytest -q tests/test_browser_smoke.py -k 'site_detail or search'
  git diff --check
  ```

**Acceptance:** The first wave makes the nine researched places materially more informative, with source traceability and conservative access language, while unsupported fields remain visibly unfilled.

**Commit:** `content: curate first marker enrichment wave`

## Task 4: Turn known identity conflicts into an explicit curator queue

**Files:**

- Append a review table to the local working report `research/marker-research-2026-09-19.md`; keep it out of the runtime overlay until decisions are approved.
- Update the private Obsidian curator queue at `Personal/Projects/Bunkerkartet/Kuratorkø - 14-09-2026.md` with the same queue references; link the queue from `Bunkerkartet.md` only if a project-level pointer is useful.
- Do not modify site rows, coordinates, statuses, or merge records in this task.

- [ ] Add queue item `CQ-011` for `krigskart:2660` vs `krigskart:2672` (Ormhaugen), requiring source-level identity comparison before either record is changed.
- [ ] Add queue item `CQ-012` for `krigskart:2667` vs `krigskart:2671` (St. Hanshaugen), requiring confirmation that the markers are distinct places or two records for one place.
- [ ] Add queue item `CQ-013` for `krigskart:413` vs `krigskart:423` (Jonsvatnet), requiring comparison of names, coordinates, site type, and source context.
- [ ] Add queue item `CQ-014` for `krigskart:2659` versus `krigskart:3154`/`krigskart:3169` (Munkholmen battery versus underwater aircraft-wreck records), explicitly preventing a thematic match from becoming a merge.
- [ ] Add queue item `CQ-015` for `krigskart:334` vs `krigskart:2676` (Østmarkneset), keeping the distinct site types separate unless primary evidence proves otherwise.
- [ ] Give every queue item the same fields: conflicting keys, source evidence, uncertainty, proposed action, owner decision, and decision date/evidence.
- [ ] Mark ambiguous records as `identity_review` or leave them outside the curated overlay until the owner decision exists; when one marker is independently well-supported, it may remain `curated` while the conflicting counterpart stays outside the overlay. Never resolve the relationship by title similarity, proximity alone, or a search result snippet.
- [ ] Re-run the 85-key report check after queue annotation to prove that the queue did not drop or duplicate a marker.

**Acceptance:** Every known identity conflict has a visible next action and an owner decision gate; no automatic merge or silent suppression occurs.

**Commit:** `docs: record marker identity review queue`

## Task 5: Verify the staged result and prepare a human review preview

**Files:**

- Create `docs/verification/2026-09-19-marker-enrichment-rollout.md`.
- Review the complete diff across `app/enrichment.py`, `app/main.py`, `app/static/app.js`, `app/content/site_enrichment.json`, the affected tests, `docs/CURATION_POLICY.md`, and the local research report.
- Do not alter `data/`, production SQLite, deployment manifests, or live records.

- [ ] Run the complete local suite and require a clean result:

  ```bash
  python3 -m pytest -q
  node --check app/static/app.js
  git diff --check
  ```

- [ ] Inspect the generated API/detail preview for Leira batteri and at least two first-wave sites; verify that source links, review dates, claim certainty, and access wording are visible and consistent.
- [ ] Verify one site with no overlay renders `Ikke kuratert i kartet`, not a claim that source material is absent.
- [ ] Verify the five identity-review families remain separate and no database status, coordinate, or access field changed.
- [ ] Perform only read-only live checks against `/api/health` and the protected `/api/sites` endpoint; record the observed response and do not call import/commit/deploy endpoints.
- [ ] Save the verification record with the branch/worktree, 85-row report count, overlay key count, focused/full test results, browser result, live read-only response codes, and explicit non-claims.
- [ ] Review the diff for secrets, copied long source text, precise directions to sensitive sites, unsupported access claims, and accidental generated/build artifacts.
- [ ] Leave the branch ready for a later owner review. A later execution step may create a preview/PR, but this plan does not authorize production import, merge, or deploy.

**Acceptance:** Tests pass, the UI is honest about research and curation state, the first wave is source-backed, the identity queue is explicit, and no live state has been mutated.

**Commit:** `test: verify staged marker enrichment rollout`

## Handoff

After the plan is reviewed, execute it in the isolated worktree in task order. The recommended execution mode is native execution in this task because the changes are tightly coupled through one overlay schema and one frontend renderer; a separate subagent is only useful for an independent final review after the focused test cycles pass.
