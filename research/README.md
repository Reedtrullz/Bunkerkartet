# Research import kit

Use the maintained application source and Python 3.12, matching CI and the
container. When the current checkout is historical or contains WIP, inspect
its branch, commit, and dirty state first; use a separate maintained-source
worktree and keep its data directory separate. Do not reset or clean a checkout
to make the quickstart work.

The recommended pipeline is:

1. Give a source URL and the relevant text to an external LLM or research tool
   with [`llm-import-prompt.md`](llm-import-prompt.md).
2. Save the JSON response locally. Research packages and source excerpts may
   contain private working material; keep them out of Git.
3. Validate the package before opening the app:

   ```sh
   .venv/bin/python research/validate_import.py path/to/package.json
   ```

4. In Bunkerkartet, choose the file, preview it, inspect the evidence and
   warnings, and commit only after review. Imported records remain `candidate`.

For a disposable local server, follow the [local quickstart](../README.md#kjør-lokalt).
The API example below defaults to that server and uses only its synthetic
token. Leave the server running in another terminal and provide a local
`package.json`.

```sh
set -eu
export BUNKERKARTET_URL="${BUNKERKARTET_URL:-http://127.0.0.1:8765}"
export ADMIN_TOKEN='synthetic-local-only'
PACKAGE_DIR=$(mktemp -d)
trap 'rm -rf "$PACKAGE_DIR"' EXIT INT TERM
PACKAGE="$PACKAGE_DIR/package.json"
cat > "$PACKAGE" <<'JSON'
{
  "schema_version": "1.0",
  "batch_id": "synthetic-quickstart-1",
  "generated_at": "2026-10-03T12:00:00Z",
  "records": [{
    "external_key": "synthetic:quickstart",
    "name": "Synthetic quickstart site",
    "site_kind": "bunker",
    "geometry": null,
    "precision": "unknown",
    "uncertainty_m": null,
    "location_basis": "explicit_coordinate",
    "status": "candidate",
    "access": "unknown",
    "sources": [{
      "url": "https://example.invalid/synthetic",
      "title": "Synthetic source",
      "source_type": "fixture",
      "excerpt": "Synthetic fixture only."
    }]
  }]
}
JSON

PREVIEW=$(curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/preview" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary "@$PACKAGE")
PREVIEW_HASH=$(printf '%s' "$PREVIEW" | .venv/bin/python -c 'import json, sys; print(json.load(sys.stdin)["preview_hash"])')

curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/commit" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "X-Import-Preview: $PREVIEW_HASH" \
  -H 'Content-Type: application/json' \
  --data-binary "@$PACKAGE"

# Retry the identical request with the same batch_id and preview hash.
curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/commit" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "X-Import-Preview: $PREVIEW_HASH" \
  -H 'Content-Type: application/json' \
  --data-binary "@$PACKAGE"

# Read the committed candidate from the local catalog.
curl --fail-with-body -sS "$BUNKERKARTET_URL/api/sites" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

If you intentionally change `BUNKERKARTET_URL`, confirm the target before
running a write. Never put production tokens in a package, shell history,
source control, or command output. Keep preview as the review gate; matching
the same `batch_id` on an identical retry makes import idempotent.

The validator uses the same strict Pydantic contract as the API. Keep evidence
compact and source-backed. Do not copy full articles or images into a package,
invent coordinates, or treat an approximate marker as permission or a
field-verification result. See [`app/imports.py`](../app/imports.py) for the
schema and the main [README](../README.md) for application boundaries.
