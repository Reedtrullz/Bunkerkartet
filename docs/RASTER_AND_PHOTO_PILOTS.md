# Raster calibration and photo attachment pilots

Status: bounded, local APIs prepared for parent-router integration. These files do not activate either product workflow. No historical raster or private photograph was supplied, fetched, copied into the repository, or treated as licensed. The calibration example and image bytes in tests are synthetic. Independent review and owner policy decisions remain pending.

## BK-64 / issue #76 — historical raster calibration evidence

`app.raster_evidence` is a pure calculation module. It has no database, approach/access, annotation, map-tile, file-loader, or network dependency. A parent route can call:

```python
from app.raster_evidence import CalibrationPolicy, build_calibration_receipt, validate_calibration_receipt

receipt = build_calibration_receipt(
    raster_descriptor,
    control_points,
    rights_status="unknown",  # unknown | restricted | permission_recorded
    rights_reference=None,
    policy=CalibrationPolicy(),  # disabled by default
)
check = validate_calibration_receipt(receipt, policy=CalibrationPolicy())
```

The raster descriptor carries an owner-provided identifier, SHA-256, pixel width/height and optional source date. It is metadata only; this module does not open or fetch the raster. At least three distinct, non-collinear control points are required. Coordinates are bounded to the raster's `[0, width-1] × [0, height-1]` pixel extent and WGS84 longitude/latitude axes (`[-180, 180]`, `[-90, 90]`). The pilot caps points, image dimensions and total pixels; antimeridian-spanning controls are explicitly unsupported.

The fit is a least-squares affine transform from pixel `(x, y)` to WGS84 `(longitude, latitude)`. The receipt stores the input controls, two affine coefficient triples, per-control residual estimates, RMS/maximum residual, estimated x/y ground scale, guardrails, rights state, gate reasons and a canonical SHA-256. Residuals/scales use a documented local spherical approximation; this is not a general geodesic or survey-grade error model. Large-area, polar, antimeridian and map-projection behavior requires geospatial-owner review. Recalculating after removing a control point produces a new matrix/residual set and receipt digest.

The default policy is disabled and leaves study-specific maximum residual and minimum/maximum scale limits unset. Comparison eligibility requires an enabled owner policy ID, `permission_recorded` rights whose reference is on that policy's approved list, explicit scale/residual limits, and an independent-review reference. The receipt labels a supplied review reference as `reference_supplied_not_independently_verified`; code cannot prove that a human review occurred. `permission_recorded` is likewise a caller-supplied state; code cannot prove a license. No real rights record, owner approval or independent review is present in this slice. Test policies and references are synthetic and do not constitute owner decisions.

No map overlay is included. A calibration receipt never writes annotations, site coordinates, approach/access state, confidence or trust; it cannot auto-promote a pixel alignment into a research claim. A missing/unavailable raster is simply not passed to this pure calculation API and does not mutate research state. The existing browser research flow was not changed or certified by this worker.

## BK-65 / issue #77 — bounded photo attachment storage

`app.image_attachments` exposes the router-facing functions:

```python
from app.image_attachments import AttachmentPolicy, store_image, read_image, list_images, delete_preview

policy = AttachmentPolicy()  # disabled; store/read fail closed
receipt = store_image(private_directory, bounded_bytes, policy, declared_mime="image/jpeg")
pixels = read_image(private_directory, receipt["attachment_id"], policy)  # sanitized derivative
items = list_images(private_directory, policy)
deletion = delete_preview(private_directory, receipt["attachment_id"], policy)
```

An enabled policy must explicitly supply an owner policy ID, recorded-rights reference, original-retention choice and bounded retention period. Current numeric defaults and hard ceilings are engineering limits for implementation and synthetic tests, not owner-approved retention or upload policy. The module cannot prove the stated rights. The default policy is disabled and creates no store directory on a rejected write.

Only byte input with PNG or JPEG magic is accepted. The declared MIME, when supplied, must match both the signature and Pillow's decoded format. SVG, HTML, animated/multiframe images, spoofed types, malformed data, excessive encoded bytes, dimensions or pixel counts fail before any attachment is written. There is no URL input or fetch path. Pillow verifies and fully decodes accepted images within the configured limits. The normalized derivative is re-encoded without source metadata; a canary test checks the derivative and thumbnail. The original SHA-256 is recorded separately. Raw input bytes are stored only when the explicit policy says `retain_original=True`; in that case the receipt warns that the original may contain metadata. Otherwise the raw original is not stored.

The server creates random 128-bit hexadecimal IDs and fixed filenames. The store and per-image directories are owner-only (`0700`); files and receipts are `0600`. Reads validate the owner-policy identity, stored receipt, immutable reference and variant SHA-256 values. The default read returns the sanitized derivative; original reads fail when originals were not retained. Listing returns stable `bunkerkartet-image:<id>` references. Delete preview reports affected variants/digests/references and explicitly performs no deletion. This module exposes no destructive delete function. Any future deletion requires a separately authorized owner command plus parent-side database-reference reconciliation.

`stage_backup` and `restore_staged_backup` are bounded in-memory fixture staging helpers. Tests qualify byte-for-byte derivative, thumbnail, optional original, receipt and immutable-reference inclusion; restore requires a new destination and refuses overwrite. They are not a production archive format and do not integrate with the existing backup/restore scripts. The parent still owns authentication, database metadata/references, archive integration, retention execution and end-to-end restore qualification.

## Pillow dependency recommendation

Pillow is absent from the repository's prepared `.venv` initially. For the synthetic implementation tests, **Pillow 12.3.0** was installed only into that local `.venv`; no requirements file was changed. As checked on 2026-10-03, PyPI lists 12.3.0 as the latest release and declares Python `>=3.10`, including CPython 3.12. The [Pillow release notes](https://pillow.readthedocs.io/en/stable/releasenotes/index.html) identify 12.3.0 as the 2026-07-01 release; PyPI lists its release files and Python classifiers on the [Pillow project page](https://pypi.org/project/pillow/).

Recommended dependency for the parent to review and pin in `requirements.txt`: `Pillow==12.3.0`. The parent should apply that change, regenerate the CI dependency receipt, and build the final image after schema work is complete. This worker did not edit requirements or rebuild the container.

## Remaining review gates

- BK-64 needs an owner-provided licensed raster, verifiable rights evidence, source date, control-point provenance, study-specific limits and an independent reviewer. None is available here; the sample is synthetic only.
- BK-65 needs the owner rights/threat-model and metadata/retention decision, authentication and database integration, a parent-owned backup/restore archive path, and owner trial. No real photo was used. The enabled policies in tests are fixtures only.
- Parent integration owns router auth, database references, browser presentation, and full backup/restore acceptance. No API endpoint, UI, live data, production storage or destructive retention action was added or run.
