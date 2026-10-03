# Authenticated API contract 1.0

`GET /openapi.json` describes the real HTTP bearer security scheme and current request/response models. The contract version is independent of the application commit returned by `/api/version`. Examples and schemas contain no credentials or private records. `/api/config` exposes only optional browser policy; it never returns the bearer or routing key.

All catalogue, research, route and import operations require `Authorization: Bearer …`. Missing/incorrect credentials return 401; an unconfigured owner credential fails closed. Credentials remain in client memory. Health/readiness/version/config contain only operational metadata.

Site list/detail use stable integer IDs, external keys and positive revisions. Null geometry/uncertainty are unknown; `access: unknown` is not permission. `data_status: unavailable` distinguishes corrupt persisted records from valid empty data. Unavailable saved routes emit neither geometry nor GPX. Historical citation metadata is explicitly separate from source registry metadata.

Protected edits require the revision the client actually observed. Missing input is 422; stale revisions are 409 with `detail.code: REVISION_MISMATCH`. A merge also requires the observed target revision. Clients migrating from implicit-revision writes must read detail, retain that revision in the draft, submit it, and reconcile a conflict before another deliberate submission. Reading a fresh revision solely to mask a stale draft is not reconciliation.

Validation responses include bounded locations/types/messages and exclude raw input. Body size violations are 413. Duplicate JSON keys, nonstandard numbers, excessive nesting and unknown request properties fail validation. Imports accept explicit 1.0 and 1.1 model schemas; see [IMPORT_CONTRACT.md](IMPORT_CONTRACT.md). An ambiguous import commit can be reconciled by batch identity and immutable receipt.

Route `request_id` is optional for legacy clients, recommended for new ones. The same ID/input returns the same saved plan; a changed payload is 409 `IDEMPOTENCY_CONFLICT`. A pending calculation is 409 `ROUTE_PENDING` with Retry-After. Provider contention is bounded and returns 429. Provider calls do not hold SQLite write transactions. An abort does not prove a mutation was cancelled; reconcile before explicitly retrying.

Private questions/assertions use their own revisions and are absent from ordinary catalogue exports. Lists with `before` cursors page over descending immutable integer IDs. Clients should retain last-good data with a visible stale label after a failed read and use latest-request-wins for navigation.
