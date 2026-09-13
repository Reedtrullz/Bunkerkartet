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

The validator uses the same strict Pydantic contract as the API. The example
package is a schema fixture only; researched batches should remain local or be
uploaded through the authenticated import endpoint rather than committed to
this public repository.

Keep the evidence compact and source-backed. Do not copy full articles or
images into the package, do not invent coordinates, and do not treat an
approximate marker as permission or a field-verification result.
