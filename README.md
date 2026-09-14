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

The candidate queue can be narrowed by confidence, access, uncertainty radius,
and source type; each row also exposes its evidence count and detail view.
The review lifecycle is `candidate` -> `researched` -> `field verified` ->
`confirmed` (or `rejected`); explicit controls also cover approximate and
destroyed-or-filled records. Status changes go through the lifecycle controls;
site details can edit coordinates, uncertainty, rationale, access, and warnings.
Field observations are dated notes with an
outcome, observed coordinates, access notes, and external photo URLs; saving an
observation does not promote a site automatically.

Photo URLs are references only. The app does not copy or host image files.
Recorded observations with coordinates appear as separate map points. A found
observation can be reviewed from the site detail and adopted as the site
coordinate; the existing uncertainty is preserved and the change is audited.
The field shortlist contains only sites explicitly marked `public`, with
candidate or researched status and coordinates, ordered by confidence and
uncertainty. Unknown access is intentionally excluded.

The site search covers names, location clues, and source excerpts. Saved routes
can be reloaded after refreshing the page, and the authenticated map can export
the visible catalogue plus coordinate-bearing observations as GeoJSON.

For external forum/newspaper research, use the small [research import
kit](research/README.md): give an LLM the extraction prompt, save its JSON
response, validate it locally, then preview and commit it in the app.

## Checks

```sh
.venv/bin/python -m pytest -q
```
