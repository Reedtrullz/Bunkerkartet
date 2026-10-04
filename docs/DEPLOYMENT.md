# Deployment

The live service uses the published immutable `main` image through the
authorized Ansible path. Docker Compose runs the app on a named data volume and
binds its selected port to loopback; Caddy terminates TLS and proxies to the
app. No DNS or Caddy configuration is managed by the playbook.

## Build and publish

GitHub Actions runs Python 3.12 tests, browser smoke, frontend syntax checks,
and a non-root synthetic container smoke. A push to `main` publishes the
image with the full commit SHA tag and records its digest in the workflow
summary. Deploy the digest and SHA together. The digest is the runtime
identity; never deploy `latest` or a mutable branch tag.

## Runtime checks

`/api/health` is liveness. `/api/ready` checks database integrity/schema,
admin authentication configuration, and optional routing configuration; it
returns `503` until the database and private API authentication are ready.
`/api/version` reports the configured application version. The deployment
playbook requires ready database/authentication and an exact full-SHA version
match before producing its release receipt.

Import preview and commit reject request bodies over 2 MiB before JSON
parsing, packages over 500 records, records with more than 20 sources, or more
than 30 warnings of 1,000 characters each. Routing permits two concurrent
OpenRouteService calls and returns `429` with `Retry-After` when both slots
are occupied.

## Ansible deployment

Copy `deploy/inventory.example.yml` to the ignored `deploy/inventory.yml` and
fill in the exact commit SHA, image digest, and the schema version reported by
that exact image's `app.db.CURRENT_SCHEMA_VERSION`; use the same value for the
backup and rollback schema fields. Also provide the owner-only backup
archive/receipt/checksum, selected loopback port, and current rollback image,
schema, volume, and checksum. Prepare `.env` on the host with owner-only
permissions and a nonempty `ADMIN_TOKEN`; the playbook checks its presence
without displaying the token.

The playbook verifies private environment-file permissions, image-tag digest,
backup checksum and schema, and compatible rollback coordinates before
Compose replacement. It uses the selected port for its readiness and exact
version checks and writes a redacted release receipt after both pass. Failed
preflight does not replace the running service. A failed post-start readiness
check retains the old volume and backup for a reviewed rollback; the playbook
does not overwrite data automatically.

```sh
ansible-playbook -i deploy/inventory.yml deploy/site.yml
```

The receipt is stored in `/srv/backups/bunkerkartet-releases/`. Keep the
inventory private because it contains infrastructure coordinates; it must
not contain credential values.

## Backup, restore, and governance

Use the bounded SQLite backup utility, staged restore procedure, UID `10001`
write qualification, failure-retention checks, receipt format, and branch
protection dry-run in [`operations/README.md`](operations/README.md). The
existing read-only verifiers are called by the new utilities. Restore writes
to a new staged path and never clears an active volume.

The 4 October execution selects twice-daily encrypted snapshots, hourly
verified copies to the owner Mac, a 24-hour recovery-point target and a
15-minute recovery-time target. See [scheduled recovery operations](operations/SCHEDULED_RECOVERY.md)
and [`operations/backup-policy.json`](operations/backup-policy.json). These
files are deployment inputs; host receipts establish actual installation.
Automatic archive/private-record erasure stays disabled. Solo-owner governance
requires up-to-date CI and PRs, administrator enforcement and no bypass,
force-push or branch deletion; [`operations/release-governance.json`](operations/release-governance.json)
records the selected policy and activation order.

## Caddy

Copy the block from `deploy/Caddyfile.example` into the host Caddy
configuration and reload Caddy using the host's normal service manager. It is
prepared for `bunker.reidar.tech`. Compose binds the selected application port
to loopback. The example enables one-year HSTS; use it only when the hostname
is permanently HTTPS-only.

### Schema-changing releases

A pre-migration backup and rollback image describe the **previous** schema. They must remain paired. Do not relabel a schema-8 backup as the candidate schema or mount a migrated volume in an older image. `scripts/verify_database.py --expected-version 8` now qualifies the actual supported historical structure read-only. `backup_database.py` records the actual source schema and refuses SQLite-only backups with active image references.

Before a schema-changing rollout, restore the retained archive to a new candidate staging directory with `scripts/qualify_migration.py --archive ARCHIVE --destination NEW_STAGE --source-version PREVIOUS_VERSION --candidate-data-volume NEW_VOLUME --rollback-data-volume OLD_VOLUME`. The command migrates only the staged copy, checks schema/integrity, verifies the rollback archive stayed unchanged, and writes `migration-qualification.json`. It does not prove runtime readiness. Populate the corresponding separate candidate volume, qualify the exact candidate image and private authentication there, and retain the old image/volume/archive pair. Supply the reviewed qualification file as `bunkerkartet_migration_qualification_path`; Ansible and the release receipt reject a schema-changing rollout without distinct volume identities and matching qualification. Port, deployment host and actual rollout remain owner-gated.

Owned photos use the full media workspace format (`app.media_exchange`), which includes qualified derivatives, thumbnails, optional originals, receipts and database references. SQLite-only backup/export refuses active photo references. No real photos or retention job were enabled by this implementation.

### Explicit private route erasure

The authenticated owner can preview exact route IDs with `POST /api/admin/retention/routes/preview` and commit the identical selection/reason with its `preview_hash` at `/commit`. No automatic schedule is active. The transaction clears names, starts, waypoints, stop snapshots, geometry, GPX, metrics and calculation details. Identity tombstones prevent SQLite ID reuse and make original request retries return HTTP 410 without recalculating. `/api/admin/retention/routes/receipts` retains the selection/reason/hash, without coordinate or name content. Site evidence is unchanged. The preview identifies visit snapshots that copied the selected route; these, exported files and pre-existing backups retain their own lifecycle and purge lag. This is logical erasure from app reads, not a claim of SQLite secure deletion or destruction of backups.
