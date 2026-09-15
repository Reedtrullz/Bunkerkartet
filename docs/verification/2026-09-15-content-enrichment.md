# Content enrichment verification

## Scope

- Ten source-backed enrichment records are loaded from
  `app/content/site_enrichment.json` using the strict schema in
  `app/enrichment.py`.
- Enrichment is keyed by the existing `external_key`; it does not alter the
  database name, status, coordinates, access field, route eligibility, source
  history, or edit flow.
- Claim-level source links and certainty labels distinguish `Kildestøttet`,
  `Uavklart`, and `Ikke dokumentert`. The database remains visible under the
  collapsed `Datagrunnlag` section.

## Display boundaries

- Enriched cards show `Om stedet`, `Hva finnes her i dag`, and separate
  `Fysisk tilgjengelighet` / `Adgangsregler` sections.
- The exact KrigsKart coordinate rationale and candidate-point warning are
  import boilerplate and appear only inside expanded `Datagrunnlag` for the
  enriched records. Other registered descriptions and warnings remain visible
  and are labelled as registered.
- Sites without enrichment retain visible fallback sections. A stored
  `condition` is shown as `Registrert tilstand` in `Hva finnes her i dag`.

## Verification

The focused browser checks cover enriched display/search, boilerplate folding,
technical metadata, source preservation, fallback content, and registered
warnings. The full pytest suite is the release-local check; no deployment,
push, production database write, or Obsidian update is part of this change.

## Final results — 2026-09-15

- Implementation commit: `8456d4c` on `codex/site-content-enrichment`.
- Full `.venv/bin/python -m pytest -q`: **163 passed, 1 existing deprecation warning**, including 22 Chromium tests.
- Coordinator independently checked Node 22.22.3 syntax and `git diff --check`.
- Coordinator verified all ten external keys against authenticated read-only production responses; the 50 statements match the researched pilot JSON exactly. This is source checking, not field verification.
- Local Chromium: searching `Junkers` finds the Ju 88 card; the 390px detail view has no horizontal overflow. Desktop and mobile images were visually inspected.
- Marker focus regression uses real keyboard focus and Enter, without a synthetic JavaScript click.
- Database schema remains v8; no database migration, production mutation, push or deployment.
