# Complete database and media backups

`backup_database.py` produces a consistent, checksummed SQLite backup. It is
the normal database recovery point and intentionally refuses to represent an
active photo workspace by itself. When image references are active, use the
complete media workspace format from `scripts/media_workspace.py`; it packages
the database, seed, immutable image references, private image receipts, and
all policy-retained original/derived/thumbnail bytes as one bounded archive.
Both formats verify without overwriting an existing archive.

Image storage, backup scheduling, offsite transfer, automatic retention, and
deletion remain disabled until an owner records the required rights, retention,
RPO/RTO, destination, and security choices. This guide creates no schedule and
does not authorize a production run. Use synthetic fixtures for local drills.

## Owner policy input

Every media CLI command requires an explicit bounded JSON policy file. Its
disabled starting form is:

```json
{
  "enabled": false,
  "owner_policy_id": null,
  "rights_status": "unknown",
  "rights_reference": null,
  "max_bytes": 8000000,
  "max_pixels": 8000000,
  "max_dimension": 7000,
  "thumbnail_max_edge": 512,
  "retain_original": null,
  "retention_days": null,
  "max_store_bytes": 100000000,
  "max_attachments": 1000,
  "deletion_enabled": false
}
```

The CLI rejects unknown keys, duplicate JSON keys, files larger than 32 KiB,
and missing policy files. The application settings file accepts the
`AttachmentPolicy` fields only; the CLI owner file also accepts the separate
`deletion_enabled` boolean. Keep the two explicit files aligned after an owner
decision. Storage requires a nonempty policy identifier, `permission_recorded`
rights plus a reviewable reference, a deliberate original-retention choice,
and bounded retention days. The example above cannot export or stage media.
Do not enter credentials or private photo data into a policy file.

## Export a complete media workspace

Use an owner-controlled backup directory with restrictive permissions and a
new UTC-stamped archive name. The CLI prints a JSON receipt with its SHA-256,
format, reference list, and member count after the parent archive verifier has
checked the contents. Save that output alongside the archive using the local
operations process; never reuse an existing archive path.

```sh
set -eu
umask 077
PROJECT_ROOT='/path/to/reviewed/Bunkerkartet'
DATA_DIR='/path/to/owner-controlled/data'
BACKUP_DIR='/path/to/owner-controlled/backups'
OWNER_POLICY='/path/to/owner-only/media-policy.json'
SEED='/path/to/reviewed/site_enrichment.json'
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="$BACKUP_DIR/bunkerkartet-media-$STAMP.bkmw"

"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/media_workspace.py" export \
  --database "$DATA_DIR/bunkerkartet.sqlite3" \
  --seed "$SEED" \
  --attachments "$DATA_DIR/attachments" \
  --archive "$ARCHIVE" \
  --project-root "$PROJECT_ROOT" \
  --policy "$OWNER_POLICY"
```

The database and `attachments` paths must be siblings in the same workspace.
The command checks active DB references against the private attachment store,
validates every image file through the parent attachment verifier, and compares
each active database receipt with its filesystem receipt before packaging.
The archive is refused if the two receipt sets differ. A normal database-only
backup remains useful for workspaces without active images; it is not a
complete-media recovery point.

## Restore to a new staging workspace

Choose a destination that does not exist and stage as the application writer
(UID `10001` in the container) or create the staging volume with ownership set
to that UID first. Do not point `--destination` at the running workspace. The
restore validates archive bounds, member paths/types, checksums, database
schema, attachment receipts, references, and retained bytes before accepting
the staged tree. Existing destinations are never replaced.

```sh
set -eu
PROJECT_ROOT='/path/to/reviewed/Bunkerkartet'
ARCHIVE='/path/to/owner-controlled/backups/bunkerkartet-media-REPLACE_WITH_UTC_TIMESTAMP.bkmw'
OWNER_POLICY='/path/to/owner-only/media-policy.json'
NEW_STAGING_DIRECTORY='/path/to/new/empty/bunkerkartet-restore-candidate'

"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/media_workspace.py" stage \
  --archive "$ARCHIVE" \
  --destination "$NEW_STAGING_DIRECTORY" \
  --policy "$OWNER_POLICY"
```

The resulting layout contains `data/bunkerkartet.sqlite3`,
`data/attachments/`, and `app/content/site_enrichment.json`. Before considering
promotion, run a candidate instance against that staged tree with the exact
reviewed image, an owner-only authentication file, and an unused loopback
port. Verify the API readiness and exact version, image reads, and representative
site content. For a local synthetic fixture, `tests/test_media_workspace_cli.py`
performs full archive export/stage and compares the restored image bytes and
site API response with the source fixture.

Keep the source archive, active volume, rollback image, and receipts while
qualifying the candidate. If copy, UID `10001` write probe, startup, or readiness
fails, retain both source and staged evidence for recovery. Do not replace the
active workspace or delete either volume as part of this local procedure.

## Explicit owner deletion

There is no retention timer, cleanup job, or API delete endpoint. A deletion
proposal is read-only and binds the owner reason, active database row, site
revision, DB receipt hash, immutable image reference, and byte hashes/sizes for
the original when retained, derived image, thumbnail, and on-disk receipt:

```sh
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/media_workspace.py" delete-preview \
  --database "$DATA_DIR/bunkerkartet.sqlite3" \
  --attachments "$DATA_DIR/attachments" \
  --attachment-id 'SERVER_GENERATED_32_HEX_ID' \
  --reason 'OWNER_REVIEWED_REASON_WITHOUT_CREDENTIALS' \
  --policy "$OWNER_POLICY"
```

Review the full output and only then run the separate command with the exact
`preview_sha256` just reviewed:

```sh
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/media_workspace.py" delete-execute \
  --database "$DATA_DIR/bunkerkartet.sqlite3" \
  --attachments "$DATA_DIR/attachments" \
  --attachment-id 'SERVER_GENERATED_32_HEX_ID' \
  --reason 'OWNER_REVIEWED_REASON_WITHOUT_CREDENTIALS' \
  --preview-sha256 'COPY_THE_REVIEWED_64_HEX_PREVIEW_HASH' \
  --policy "$OWNER_POLICY"
```

The owner file must explicitly set `deletion_enabled` to `true` in addition to
the attachment policy being enabled. The command rechecks the preview under a
SQLite write transaction, tombstones the immutable DB reference before removing
bytes, records a site event, and writes a mode-`0600` recovery receipt under
`DATA_DIR/media-deletion-receipts/`. The DB receipt/provenance and tombstone are
preserved. If byte removal is interrupted, the reference stays unavailable and
the receipt lists verified deleted and remaining paths/hashes for manual
recovery. Do not rerun with a guessed hash or remove files outside the exact
server-generated attachment directory.

The synthetic deletion test verifies retained-original, derived, thumbnail,
receipt, DB reference, provenance tombstone, event, recovery manifest, and API
unavailability. Its injected partial-failure case confirms that remaining
bytes stay recorded for recovery. It does not grant rights over any real image.

## Current operator acceptance gates

The archive and deletion drills are local implementation evidence only. Owner
decisions are still required for image rights, original retention, backup
cadence/RPO, restore objective/RTO, encrypted offsite provider and credentials,
retention and purge lag, and authorization for any real data operation. No
private photos or credentials were used in the synthetic tests.

Schema-changing rollout requires distinct candidate and rollback volumes. The
migration qualification binds the exact checkpointed database size and SHA-256;
the playbook mounts the candidate volume read-only in the digest-pinned image and
checks those bytes, schema and integrity before startup. Empty, stale or altered
candidate volumes fail before replacement. The operator still must copy the
staged database into that selected volume; matching volume names alone are not
qualification evidence. Active WAL/journal residue is rejected for this stopped
candidate; the rollback volume and retained archive stay separate.
