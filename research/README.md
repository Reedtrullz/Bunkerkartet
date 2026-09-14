# Research import kit

The intended pipeline is:

1. Give a source URL and the relevant text to an external LLM with
   [`llm-import-prompt.md`](llm-import-prompt.md).
2. Save the JSON response locally.
3. Validate it before opening the app:

   ```sh
   .venv/bin/python research/validate_import.py path/to/package.json
   ```

4. In Bunkerkartet, choose the file, preview it, inspect the evidence and
   warnings, then commit it. Imported records remain `candidate` until review.

For an external LLM or a scripted batch, the same two-step import can use the
authenticated API directly:

```sh
export BUNKERKARTET_URL=https://bunker.reidar.tech
export ADMIN_TOKEN='use-a-local-token'
PREVIEW=$(curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/preview" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data-binary @package.json
PREVIEW_HASH=$(printf '%s' "$PREVIEW" | python3 -c 'import json, sys; print(json.load(sys.stdin)["preview_hash"])')
curl --fail-with-body -sS "$BUNKERKARTET_URL/api/admin/imports/commit" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "X-Import-Preview: $PREVIEW_HASH" \
  -H 'Content-Type: application/json' \
  --data-binary @package.json
```

Preview is the review gate. Keep the token in the shell environment or a
secret manager, never in the package or repository. The API schema is
available at `$BUNKERKARTET_URL/api/imports/schema` with the same bearer
header.

The validator uses the same strict Pydantic contract as the API. The example
package is a schema fixture only; researched batches should remain local or be
uploaded through the authenticated import endpoint rather than committed to
this public repository.

Keep the evidence compact and source-backed. Do not copy full articles or
images into the package, do not invent coordinates, and do not treat an
approximate marker as permission or a field-verification result.
