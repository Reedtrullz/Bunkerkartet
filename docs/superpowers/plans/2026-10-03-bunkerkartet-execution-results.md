# Bunkerkartet execution evidence — 2026-10-03

This execution covers all 65 original open issue identities BK01–BK65 (#13–77) and the five original dependency PRs. The frozen spec preserves the exact original acceptance text; the execution ledger maps every issue to concrete tests and records remaining gates. GitHub re-enumeration still shows the same 65 issues, with none newly missing or added. No issue is automatically closed by synthetic tests.

## Delivery and verification

The implementation uses the isolated `codex/bunkerkartet-all-items` worktree. The historical primary checkout, modified README and private SQLite/WAL/SHM residue are preserved. Existing dependency PRs #7, #9 and #11 are merged after head qualification; #1 is closed in favor of the retained Python3.12 runtime policy. PR12's current head has passing CI; final qualification uses an exact-source paired environment comparison, separately from discarded concurrent-WIP browser runs.

The earlier foundations/editor/research draft stack #78 → #79 → #80 has passing exact-head Linux CI. The integrated schema16 backend, browser workflows, complete media exchanges and deployment tooling are delivered as its next draft. Draft implementation and synthetic proof do not establish production rollout, factual source correctness, owner trial, rights, device acceptance or approved publication.

Local backend: 447 passed, one Docker skip before final image. Focused frontend: 22 smoke +25 browser contract +3 static checks passed; workflow browser9 passed; curator browser4 passed. The final combined suite, exact-head CI and current-image non-root restore/readiness proof are appended after completion. Python dependency consistency, four frontend syntax checks and Ansible syntax pass.

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
