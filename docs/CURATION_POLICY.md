# Bunkerkartet curation policy

This is an internal, private-by-default review policy. A green import, a
matching coordinate, a `related` relation, or a reviewed public approach is
not by itself proof of identity, access, safety, ownership, or field truth.
No catalogue status is raised and no records are merged automatically.

## Review queue

Keep the queue in a private review package or Obsidian, not in public source
data. Each item has:

| Field | Required content |
|---|---|
| Queue ID | Stable internal ID, not a guessed site key |
| Issue | One named unresolved question |
| Source basis | URLs and short source references; do not copy full source text |
| Uncertainty | What is unknown, conflicting, or inferred |
| Proposed action | Verify, retain separate, relate, reject, or defer |
| Owner decision | `eier/kurator må avgjøre` until explicitly decided |
| Decision evidence | Date, reviewer, and bounded reason after a decision |

The current named queue is kept privately in Obsidian at
`Personal/Projects/Bunkerkartet/Kuratorkø - 14-09-2026.md`. The public
repository intentionally contains only this policy and no site-by-site
decision, coordinate selection, or copied source assessment.

An identical point is a duplicate warning or possible relation, never a merge
decision. A `related` link means that the source connected two external keys;
it does not mean same-as, parent/child, or verified identity.

## Staged enrichment states

The map overlay uses four presentation states:

- `curated` — source-backed claims have been reviewed for the overlay.
- `researched_pending` — relevant research exists, but the claims are not yet ready for map-visible curation.
- `identity_review` — the marker identity or relationship to another marker remains unresolved.
- `not_curated` — no overlay is present for the marker. This is a presentation state, not a claim that no online research exists.

The local 85-marker research report separately tracks `baseline_only`,
`external_found`, `identity_review`, and `curation_ready`. It must not be
replaced by the map fallback text.

## Source and coordinate rules

Use source references rather than copied full text. Treat unclear licensing as
unclear. Do not publish a coordinate without a separate positive selection for
that publication. Keep historical names and source wording intact; a
normalized category is not historical identity evidence. Do not invent a
radius, access permission, approach approval, or field observation.

For source weighting, use official or archival material for identity, ownership,
and access; specialist or local-history material for structure and wartime
history; forums, trip reports, Peakbook, and geocaching for dated observations
of approach or visible remains only; and search-result snippets for discovery
only. A visit report is not an access permission.

## Owner decisions required before broader use

The owner must decide, separately from code release:

- geographic scope and the public catalogue selection;
- which approach points are genuinely reviewed and public;
- review and merge criteria;
- retention and deletion policy for private route/observation data;
- RPO/RTO and external backup ownership;
- GitHub branch-protection bypass policy and named approvers;
- whether any key or legacy source requires a private secret-handling plan.

Until those decisions exist, the application remains a private research and
curation tool. This document is policy guidance, not an automatic production
configuration.
