# Bunkerkartet

Private, source-backed map for wartime remains around Trondheim. The app accepts
strict JSON evidence packages prepared by external research/LLM workflows; it
does not scrape sources or call an LLM itself.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
ADMIN_TOKEN='use-a-local-token' .venv/bin/uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> and enter the same admin token. Set `ORS_API_KEY`
as well when walking-route requests should use OpenRouteService. The SQLite
database is created under `BUNKERKARTET_DATA_DIR`.

## Import workflow

Use the JSON file picker in the app, preview the batch, then commit it. Every
record remains a candidate until review. Reusing a committed `batch_id` is
idempotent, and imports never replace reviewed site fields. The complete
versioned contract is in [`app/imports.py`](app/imports.py).

## Checks

```sh
.venv/bin/python -m pytest -q
```
