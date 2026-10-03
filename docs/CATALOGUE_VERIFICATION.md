# Catalogue performance, quality, and sequence verification

This private backend slice covers BK29 (observation list retrieval and a synthetic growth probe), BK61 (bounded seeded API sequences with an independent oracle), and BK62 (read-only provenance and review coverage). It does not add a dashboard or alter trust, access, location, or lifecycle state.

## BK29 target declared before optimization

The fixed synthetic fixtures contain 100 and 1,000 sites, each with exactly two coordinate-bearing observations. The target for the observation portion of a catalogue listing is at most `ceil(site_count / 500) + ceil(observation_count / 500)` SQL reads: two at 100 sites and six at 1,000 sites. The pre-change path performs one observation-list read per site and one amendment-history read per observation (300 and 3,000 reads respectively for these fixtures). The selected observation-point payload must remain byte-for-byte equivalent. The probe records wall time and peak Python allocation without setting a UI-latency target; browser rendering and full user-perceived performance are outside this backend slice.

All measurements use fresh synthetic SQLite databases and fixed deterministic data. The batched helper caps every `IN` parameter list at 500, supplies prefetched amendments to `effective_observation`, and leaves the ordinary original-observation record untouched. A parity fixture compares against the legacy point projection when original photo/context JSON or a stored amendment role/radius is corrupt; both paths omit those invalid assessments. Import effects are separately checked with a 500-record synthetic package.

## BK62 dimensions

The private read-only API reports independent identity, location, access, currentness, and evidence dimensions with explicit filtered and unfiltered denominators. It also reports unresolved research questions and a source-dependence view based on distinct linked source records; one or multiple sources do not establish independence or truth. `unknown`, unavailable, policy-disabled, due, and overdue remain distinct where applicable. Drill-down membership is derived from the same per-site states as the counts. No aggregate score, status promotion, coordinate edit, or other mutation is exposed.

## BK61 sequence bounds

The standard-library corpus uses fixed seeds, records each action trace, and checks API results against a separately maintained expected-state ledger. It covers import retries, stale edits, rejected invalid promotion, observation idempotency and withdrawal, merge transfer, research hypotheses, and saved-route revalidation. A deliberately corrupted oracle snapshot verifies that candidate promotion, payload-hash rewrite, and duplicate evidence are detected. The corpus is deliberately small; its measured local runtime is recorded in `quality-report.md` before considering expansion.

## Evidence limits

Every row, source, route, observation, question, and action used by this slice is synthetic. Backend query/payload/time/memory measurements do not establish browser rendering, owner workflow usefulness, source truth, field safety, deployment behavior, or production catalogue quality.

## Integrated list measurement (2026-10-03)

After integration, the actual authenticated `GET /api/sites` used 3 SQL statements at 100 sites and 7 at 1,000 sites, including the common site-row read; the original endpoint used 301 and 3,001 respectively. Payloads stayed 109,827 and 1,102,225 bytes. On this local macOS/Python 3.12 run, full-response times were 29.698 and 202.802 ms and peak traced Python allocations were 1,128,228 and 7,720,660 bytes. Identical observation payload hashes were retained. These synthetic samples establish bounded reads and payload parity; they do not establish production or browser latency.
