# Operations preparation

This page contains local and reviewable procedures for backup, restore,
release receipts, and GitHub release governance. The tracked policy files record the 4 October selected defaults. See
[SCHEDULED_RECOVERY.md](SCHEDULED_RECOVERY.md) for scheduled encrypted snapshots,
verified off-host copies, targets, quotas and installation evidence. Policy
files alone do not install jobs or change GitHub settings. Automatic erasure
remains disabled.

## Backup a consistent SQLite snapshot

Build or select the exact application image already under review. Run the
backup tool with the named data volume mounted read/write so SQLite can read
its WAL state while taking a consistent online snapshot. Give the backup
directory a restricted owner-only mode and enough free space; the application
volume is not modified by the backup tool.

```sh
set -eu
IMAGE='ghcr.io/reedtrullz/bunkerkartet@sha256:REPLACE_WITH_REVIEWED_DIGEST'
BACKUP_DIR='/srv/backups/bunkerkartet'
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="$BACKUP_DIR/bunkerkartet-$STAMP.tar.gz"
EXPECTED_SCHEMA=$(docker run --rm --entrypoint python "$IMAGE" -c 'from app.db import CURRENT_SCHEMA_VERSION; print(CURRENT_SCHEMA_VERSION)')

sudo install -d -o 10001 -g 10001 -m 0700 "$BACKUP_DIR"
docker run --rm \
  --volume bunkerkartet-data:/app/data \
  --volume "$BACKUP_DIR:/backup" \
  --entrypoint python "$IMAGE" \
  /app/scripts/backup_database.py create \
  --database /app/data/bunkerkartet.sqlite3 \
  --archive "/backup/$(basename "$ARCHIVE")"

docker run --rm --read-only --tmpfs /tmp:rw,nosuid,noexec,size=32m \
  --volume "$BACKUP_DIR:/backup:ro" --entrypoint python "$IMAGE" \
  /app/scripts/backup_database.py verify \
  --archive "/backup/$(basename "$ARCHIVE")" \
  --expected-schema-version "$EXPECTED_SCHEMA"

docker run --rm --read-only --tmpfs /tmp:rw,nosuid,noexec,size=32m \
  --volume "$BACKUP_DIR:/backup:ro" --entrypoint python "$IMAGE" \
  /app/scripts/backup_database.py status \
  --directory /backup --max-age-seconds 86400
```

The archive and adjacent `.receipt.json` are the recovery evidence. The tool
uses SQLite's backup API, writes a bounded tar archive, calls the repository's
archive and database verifiers, and records a SHA-256 checksum. It refuses to
overwrite an existing archive. `status --directory ... --max-age-seconds ...`
fails when there is no receipt, the newest success is stale, the archive no
longer matches its receipt, or a newer failed-run marker exists. Choose the
freshness bound from the eventual owner-approved RPO; until then, run status
with an explicit one-off interval for a local drill.

There is no retention cleanup command. Keep archives and receipts until an
owner selects and reviews an encrypted offsite destination, RPO/RTO, retention
period, backup purge lag, and an auditable deletion preview. The disabled
values are in [`backup-policy.json`](backup-policy.json). This procedure does
not remove route starts, route geometry, GPX files, observations, evidence, or
history.

## Stage and qualify a restore

The restore tool only writes to a new staging directory. It verifies the
archive member set and compressed/expanded byte limits before extraction,
calls the existing SQLite verifier, and keeps the source archive. It refuses
an existing destination, so a failed attempt cannot clear the active volume.
The staged files are mode `0600`; run the commands as UID `10001` or use the
application image to create the staging volume so that the restored writer
owns its database.

```sh
set -eu
IMAGE='ghcr.io/reedtrullz/bunkerkartet@sha256:REPLACE_WITH_REVIEWED_DIGEST'
BACKUP_DIR='/srv/backups/bunkerkartet'
ARCHIVE='bunkerkartet-REPLACE_WITH_UTC_TIMESTAMP.tar.gz'
STAGING_VOLUME="bunkerkartet-restore-$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_DATA='/app/data/restored'
EXPECTED_SCHEMA=$(docker run --rm --entrypoint python "$IMAGE" -c 'from app.db import CURRENT_SCHEMA_VERSION; print(CURRENT_SCHEMA_VERSION)')

docker volume create "$STAGING_VOLUME" >/dev/null
docker run --rm --user 0:0 \
  --volume "$STAGING_VOLUME:/app/data" --entrypoint python "$IMAGE" \
  -c "import os; os.chown('/app/data', 10001, 10001)"
docker run --rm --user 10001:10001 \
  --volume "$STAGING_VOLUME:/app/data" \
  --volume "$BACKUP_DIR:/backup:ro" \
  --entrypoint python "$IMAGE" \
  /app/scripts/restore_database.py \
  --archive "/backup/$ARCHIVE" \
  --destination "$STAGING_DATA" \
  --expected-version "$EXPECTED_SCHEMA"

docker run --rm --user 10001:10001 \
  --volume "$STAGING_VOLUME:/app/data" --entrypoint python "$IMAGE" \
  -c "from pathlib import Path; p=Path('$STAGING_DATA/uid10001-probe.sqlite3'); p.write_bytes(b'uid10001-write-ok'); p.unlink()"

COMMIT_SHA='REPLACE_WITH_FULL_CANDIDATE_SHA'
docker run --detach --name bunkerkartet-restore-candidate \
  --env-file /opt/bunkerkartet/.env \
  --env BUNKERKARTET_DATA_DIR="$STAGING_DATA" --env APP_VERSION="$COMMIT_SHA" \
  --publish 127.0.0.1:18000:8000 \
  --volume "$STAGING_VOLUME:/app/data" "$IMAGE"
curl --fail-with-body -sS http://127.0.0.1:18000/api/ready
curl --fail-with-body -sS http://127.0.0.1:18000/api/ready | python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["database"] == "ready" and r["authentication"] == "configured"'
test "$(curl --fail-with-body -sS http://127.0.0.1:18000/api/version | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')" = "$COMMIT_SHA"
```

Keep the currently active image digest, active volume name, and a verified
pre-restore archive together. Start the candidate image against the staged
volume on an unused loopback port with the existing owner-only `.env` file;
check `/api/ready` reports database `ready` and authentication `configured`,
then check `/api/version` against the exact full commit SHA. Do not send public
traffic to the candidate during qualification. The separate compose volume
and prior image remain available if extraction, copy, start, or readiness
fails. Record the failure and retain both the source archive and active
volume. Do not automatically restore over writes newer than the selected
archive; pause for the owner to resolve that recovery boundary.

If candidate start or readiness fails, stop only that candidate and keep the
staging volume, active volume, image digests, and backup receipts for diagnosis:

```sh
docker rm --force bunkerkartet-restore-candidate || true
```

Only after the owner selects the recovery point, stop the active writer, take
and verify a final backup of the active volume, and review any writes since
the chosen restore point. Promote by changing the compose data-volume name and
image digest together, then recheck readiness and exact version. Retain the
previous volume, previous image digest, and both backup receipts until the
new service is accepted. Rollback restores the old digest and old volume as a
pair; never delete either volume as part of the qualification drill.

## Release receipt

Before deployment, provide the playbook with the owner-only `.env`, reviewed
backup archive and receipt, expected schema, selected loopback port, current
rollback image digest, active volume name, and matching rollback backup
checksum. `deploy/site.yml` checks these inputs and the image digest before
Compose replacement. It then requires `/api/ready` and `/api/version` to match
the selected release and writes a redacted receipt under
`/srv/backups/bunkerkartet-releases/`.

The same verifier can create a receipt from captured endpoint responses and a
verified backup without recording credentials or arbitrary response fields:

```sh
set -eu
COMMIT_SHA='REPLACE_WITH_FULL_CANDIDATE_SHA'
IMAGE_DIGEST='sha256:REPLACE_WITH_REVIEWED_IMAGE_DIGEST'
IMAGE="ghcr.io/reedtrullz/bunkerkartet@$IMAGE_DIGEST"
ROLLBACK_IMAGE_DIGEST='sha256:REPLACE_WITH_CURRENT_IMAGE_DIGEST'
BACKUP_SHA256='REPLACE_WITH_VERIFIED_BACKUP_SHA256'
ARCHIVE='/srv/backups/bunkerkartet/REPLACE_WITH_ARCHIVE.tar.gz'
APP_PORT=8000
EXPECTED_SCHEMA=$(docker run --rm --entrypoint python "$IMAGE" -c 'from app.db import CURRENT_SCHEMA_VERSION; print(CURRENT_SCHEMA_VERSION)')
curl --fail-with-body -sS "http://127.0.0.1:$APP_PORT/api/ready" > readiness.json
curl --fail-with-body -sS "http://127.0.0.1:$APP_PORT/api/version" > version.json
python3 -c 'import json; r=json.load(open("readiness.json")); assert r["database"] == "ready" and r["authentication"] == "configured"'

./.venv/bin/python scripts/release_receipt.py build \
  --commit-sha "$COMMIT_SHA" \
  --image-digest "$IMAGE_DIGEST" \
  --database-schema-version "$EXPECTED_SCHEMA" \
  --backup-archive "$ARCHIVE" \
  --backup-receipt "$ARCHIVE.receipt.json" \
  --selected-port "$APP_PORT" \
  --readiness-json readiness.json \
  --version-json version.json \
  --rollback-image-digest "$ROLLBACK_IMAGE_DIGEST" \
  --rollback-schema-version "$EXPECTED_SCHEMA" \
  --rollback-data-volume bunkerkartet-data \
  --rollback-backup-sha256 "$BACKUP_SHA256" \
  --output release-receipt.json
./.venv/bin/python scripts/release_receipt.py verify release-receipt.json
```

The receipt records the full commit SHA, immutable image digest, database
schema, archive checksum/name, selected port, allowlisted readiness fields,
exact API version, and image/database/volume rollback coordinates. Store it
with the operational backups; do not commit a receipt containing private
infrastructure paths.

## Branch protection dry-run

The plan snapshot recorded `main` as unprotected on 3 October 2026. Confirm
current state using read-only GitHub API calls before proposing an owner
change:

```sh
gh api repos/Reedtrullz/Bunkerkartet/branches/main/protection
gh api repos/Reedtrullz/Bunkerkartet/rulesets
```

A 404 from the branch-protection query means that endpoint has no branch
protection object; it does not prove that no organization ruleset applies.
The review draft in [`release-governance.json`](release-governance.json)
proposes requiring the `test` check, an up-to-date branch, one pull-request
approval, administrator enforcement, and no force-push/deletion. It lists no
bypass actor. The policy remains inactive until an owner reviews current
rulesets, names any permitted bypass actors, and applies the settings through
the authorized repository administration path. Existing dependency-update
pull requests remain separate review items.

## Owner credential rotation boundary

The supported single-owner rotation boundary is a restart of the reviewed image
with a replaced nonempty `ADMIN_TOKEN` in its owner-only environment. There is
no overlap window or second accepted owner key. Keep the verified database and
rollback pair, update the private environment using the owner's existing secret
tooling, restart, check exact-version readiness, and reauthenticate. Never put
the token into a shell URL, command argument, issue, receipt or browser storage.
The synthetic rotation test stops the old app, starts the same workspace with a
new token, proves the old bearer fails reads and writes, and reconciles an
ambiguous observation send with its original request ID into exactly one record.
Actual owner token rotation and offsite retention remain unapplied decisions.

## Selected-policy follow-up — 4 October 2026

The earlier commands above remain manual rehearsal examples. The current
selected operations and governance policy is in [SCHEDULED_RECOVERY.md](SCHEDULED_RECOVERY.md).
This supersedes the earlier pending-choice language for scheduling, destination,
encryption, RPO/RTO, minimum retention and solo-owner merge rules. Actual token
rotation, physical-device/screen-reader/field acceptance and private-data erasure
are still separate operations.
