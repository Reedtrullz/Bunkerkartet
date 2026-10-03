# Versioned research imports

The authenticated `/api/imports/schema` response is generated from the accepted models and declares 1.0 and 1.1. Version 1.0 retains its existing parsed payload and hash identity, including exact retries. Version 1.1 additionally accepts evidence `content_kind` (quote/summary/unknown), `role` (identity/location/access/context), bounded claim IDs, uncertainty notes, and rights status/notes. Missing rights mean unknown. A recorded rights note is an assertion requiring review; it grants no permission.

Preview shows each evidence reading before commit. Commit retains the original source title/type and metadata in immutable import records. A registry link and a dated reading are separate identities; multiple roles never multiply a reading. Protected batch summaries and details use stable integer cursors (`before`, `next_cursor`) and expose hashes, record effects and evidence IDs without returning raw private payloads. `GET /api/admin/imports/{batch_id}` reconciles an ambiguous response; a conflicting payload with the same batch ID remains rejected.

Catalogue search uses Unicode casefold and literal substring matching across effective names, claim text and individual evidence readings. Percent and underscore are literal characters, not SQL wildcards. Existing status/access/confidence/merged filters still apply. Private question text is deliberately absent from ordinary catalogue search and exports.

Private research questions may refer to an external key before import without creating a catalogue site. Deferral preserves the original wording and references. A resolution is an explicit revision-bound reasoned decision. Alternatives and claim assertion links never promote a status, replace a point or grant access.
