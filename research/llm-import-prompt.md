# LLM extraction prompt

Use this prompt with the source URL and copied source text. Ask the model to
return only the JSON object, with no Markdown fence or commentary.

```text
You are preparing a Bunkerkartet research import for wartime remains around
Trondheim, Norway. Extract one record for each distinct physical site that is
supported by the supplied source text.

Return exactly one JSON object matching the package shape in
research/example-import.json. The package must use schema_version "1.0",
include a new timezone-aware generated_at, and have a unique descriptive
batch_id. Every record must use status "candidate" and include at least one
source with the supplied URL, source title, source type, a short faithful
excerpt, and access_date.

Rules:
- Never include snublesteiner. Exclude them rather than renaming or merging
  them into another site.
- Do not invent coordinates. Use geometry null and precision "unknown" when
  the source does not support a point. If a source gives a landmark or map
  reference and you estimate a search-area point, set precision to
  "approximate", set uncertainty_m conservatively, and explain the inference
  in short_rationale and warnings.
- Use location_basis "explicit_coordinate" only for a coordinate stated by the
  source; use "address" for a stated address; "map_reference" for a usable map
  point; "landmark_description" for a geocoded landmark anchor; and
  "llm_inference" for a reasoned area estimate based on surrounding evidence.
- A geometry requires uncertainty_m in metres. Exact means genuinely precise,
  not merely a source that sounds confident.
- Keep access separate from historical identity. Use unknown unless the source
  supports public, restricted, private, permission_required, or dangerous.
  Never imply that a marker grants access, permission, or safety.
- Put demolition, burial, private property, unstable structures, missing
  entrances, and conflicting identifications in condition or warnings.
- Keep source excerpts under 2,000 characters and summarize rather than copy
  long passages. Do not include images, credentials, personal contact details,
  or hidden chain-of-thought.
- Use a stable external_key such as "kystfort:topic:123". Make separate child
  records when a source describes distinct structures, and use
  related_site_keys to connect them to known records without silently merging
  them.
- If evidence is too weak for even an honest area lead, omit the record.
```

Before import, run the local validator and inspect every coordinate, uncertainty
radius, access value, and warning yourself.
