# Bunkerkartet execution evidence — 2026-10-03

This execution covers all 65 original open issue identities BK01–BK65 (#13–77) and the five original dependency PRs. The frozen spec preserves the exact original acceptance text; the execution ledger maps every issue to concrete tests and records remaining gates. GitHub re-enumeration still shows the same 65 issues, with none newly missing or added. No issue is automatically closed by synthetic tests.

## Delivery and verification

The implementation uses the isolated `codex/bunkerkartet-all-items` worktree. The historical primary checkout, modified README and private SQLite/WAL/SHM residue are preserved. Existing dependency PRs #7, #9, #11 and #12 are merged after head qualification; #1 is closed in favor of the retained Python3.12 runtime policy. PR12 was merged only after exact-head CI and the isolated paired source comparison below, separately from discarded concurrent-WIP browser runs.

The earlier foundations/editor/research draft stack #78 → #79 → #80 has passing exact-head Linux CI. The integrated schema16 backend, browser workflows, complete media exchanges and deployment tooling are delivered as its next draft. Draft implementation and synthetic proof do not establish production rollout, factual source correctness, owner trial, rights, device acceptance or approved publication.

Local backend: 447 passed, one Docker skip before final image. Focused frontend: 22 smoke +25 browser contract +3 static checks passed; workflow browser9 passed; curator browser4 passed. The combined suite passed507 tests with one Docker skip in133.36s. Linux CI for PR81 head33377810be8174ae80f241e388e075c71d0460ad succeeded in run37135136915, including test/browser/build/non-root container steps. The local current schema16 image separately passed the populated online/WAL snapshot, UID10001 restore/transaction, exact-version ready response and missing-auth503 proof (one test,4.90s). Later final-head changes receive their own receipts below. Python dependency consistency, four frontend syntax checks and Ansible syntax pass.

The catalogue benchmark uses fixed synthetic 100/1000-site databases with two observations each. The actual full listing changed from301/3001 SQL statements to3/7, with the same109,827/1,102,225-byte payloads. On this local macOS/arm64 Python3.12 sample, response times were29.698/202.802ms and traced peak allocations1,128,228/7,720,660bytes. Observation projection hashes are identical; batching increases peak allocation. Schema16 synthetic readiness scans measured4.246–5.978ms at100 sites and8.441–8.682ms at1000 sites. Neither benchmark is a production latency claim.

Independent integration review reproduced and fixed scoped-reader access to private saved routes and restore path replacement after receipt verification. Saved routes are owner-only. Restore copies a bounded private input snapshot, verifies and extracts those same bytes, returns the verified digest and exclusively reserves the destination. Schema-changing deployment checks exact candidate DB bytes, size, integrity and schema in the digest-pinned image before startup, with distinct preserved rollback volume/archive. Standalone release receipts also require the exact candidate DB binding. No playbook was run on a host.

## Remaining acceptance gates

- BK10/25: real host, release digest, selected port, compatible image/database rollback pair, permissions and production recovery trial.
- BK16/17/57: actual freshness/review/profile policy and real source/field evidence decisions. Hiking stays the default.
- BK22: screen reader and physical mobile-device acceptance.
- BK26/28: owner-selected encrypted offsite destination, RPO/RTO, cadence, retention/purge lag, real offsite restore and approved main governance rules. Configurations remain inactive. Route deletion is explicit exact-preview erasure with audit tombstones; separately retained visit snapshots, exports/backups and SQLite secure-erasure limits are disclosed.
- BK30/31/32/63: device exposure/erase/field trial, independently reviewed historical sample, actual reader scope and separate per-record publication rights/coordinate decisions, and actual geographic descriptor. Pilots remain disabled by default; there is no publish or automatic tile-cache/sync action.
- BK42/43: real owner bearer rotation/lock timing and basemap provider/terms choices. Restart rotation and ambiguous observation reconciliation are synthetic tests; no-map defaults issue no tile traffic.
- **BK64 is partial and remains blocked:** only synthetic calibration receipts/control-point/residual tests are prepared. No licensed raster, independent reviewed sample, established georeferencing workflow or opacity/swipe renderer is delivered. It must not be represented as implemented or accepted historical-raster comparison.
- BK65: owner rights/threat model, metadata/original-retention/storage policy and actual media/offsite/deployment acceptance. Bounded decode/re-encode, metadata canaries, full bytes/references restore and explicit approved-deletion recovery are qualified with synthetic images only.

All ordinary engineering entries retain their own test mapping in the ledger. These gates are not silently treated as accepted, and real credentials, private catalogue contents, offsite archives and field data were not used.

## Current-image receipt

Image `bunkerkartet:all-items-schema16` has local image identity `sha256:ad14565b5d750823b8ff3af00ef6043a17f832bd0d74efdd5197e1099930856a`, Python3.12.14 and schema16; the build used execution source33377810be8174ae80f241e388e075c71d0460ad. The app snapshot differs from local Python3.12.13, and both runtimes passed their applicable checks. Container volumes were synthetic and removed by the test; the smoke image is retained, with no pruning of unrelated images/volumes. This is local engineering proof, not an installed production digest or offsite recovery result.

## Final lifecycle/introspection follow-up

A sites/history credential can discover its own role/scopes/expiry without receiving a catalogue scope it was not granted. History-only grants still fail catalogue and private route reads. A focused regression also proved that the StartedClient helper initialized lifespan twice inside a `with` block; its owned entry/exit is now idempotent and the regression requires exactly one initialization. Container proof takes the requested full source SHA (or CI checkout SHA), rather than always using a synthetic constant. The revised backend suite passed449 tests with one Docker skip in19.88s. These follow-up changes are separately requalified on the final PR head.

## Final dependency and source qualification

PR12 exact head7782f15426eb722c810b0b083640d702c0cbcf3f passed CI37126707965 and was merged as36f8628a1b2e4382c2ec2250497e37903cc737e5. The isolated read-only source archive had tree5aff8ab40843a5c84d902e0199d78246ea895027 and SHA2564839ac95ca470a59f7a6498715f3af89610ae83d70be73a09c7591256cb0c6e0. Python3.12.13, Playwright1.63.0/Chromium1243 and other resolved distributions matched; only httpx2 and its required matching httpcore2 pin differed. Both pip checks passed.

| Package pair | Core | Smoke1 | Smoke2 | Matched operator-flow rerun |
|---|---|---|---|---|
|2.12.0|146 pass,4.22s|22 pass,42.01s|21 pass,1 timeout,74.86s|1 pass,6.12s|
|2.13.1|146 pass,4.68s|22 pass,42.99s|22 pass,41.12s|1 pass,4.23s|

The baseline-only timeout did not reproduce. These suites provide no reproducible upgrade regression; one intermittent baseline failure limits causal confidence. An initial core collection harness error from the read-only default data path was corrected by explicit isolated BUNKERKARTET_DATA_DIR. Prior concurrent-WIP comparisons are excluded. Run-owned archives/environments were cleaned and unrelated daemons preserved.

Lifecycle/introspection source36be1f086f587571b60cc11ed55e6604f41ef48e passed Linux CI37135841265. After merging PR12, source0bc4665aa2ba20cdbf89025aeb341c83edc1de22 passed458 tests with one Docker skip in38.01s (449 core/static plus9 workflow browser tests) with httpx2/httpcore2 2.13.1, and pip check. Final exact-head CI and rebuilt-image receipts are linked from the published PR; this document records prior immutable source qualifications without a self-referencing commit hash.
