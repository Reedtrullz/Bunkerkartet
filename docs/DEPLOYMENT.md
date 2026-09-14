# Deployment

The live service is deployed from a published `main` image through the
authorized Ansible deployment path. CI builds and publishes the immutable
image; the intended runtime is a Docker Compose service on the VPS, with Caddy
terminating public TLS and proxying to the app on `127.0.0.1:8000`. The
repository also contains the Ansible path for manual or recovery deployment.

## Build and publish

GitHub Actions runs tests and builds the image for pull requests and pushes to
`main`. Only a push to `main` publishes to GHCR. The
published image receives the full commit SHA tag and the workflow summary
records its digest. Deploy both values; the Compose service runs by digest.

```text
ghcr.io/reedtrullz/bunkerkartet@sha256:0123456789abcdef...
```

Do not use `latest` or a mutable branch tag. A commit SHA tag is used to find
the published artifact; the digest is the enforced runtime identity.

## Ansible preparation

Copy `deploy/inventory.example.yml` to an ignored `deploy/inventory.yml`, then
replace the placeholder host, commit SHA, and published image digest. Public
GHCR packages can be pulled anonymously. If the package is private, provide the
server's GHCR username and read-only package token through an Ansible Vault file
or another external secret source. Do not put registry credentials in the
application `.env` or in Git.

```bash
ansible-playbook -i deploy/inventory.yml deploy/site.yml
```

The playbook requires a 40-character commit SHA and a `sha256:` digest. It
optionally logs in, pulls the SHA tag, verifies that it resolves to the expected
digest, writes the digest-pinned Compose file, and checks both `/api/health` and
its reported version. It does not configure DNS, Caddy, or application
credentials.

## Caddy

Copy the block from `deploy/Caddyfile.example` into the host Caddy
configuration and reload Caddy using the host's normal service manager. It is
prepared for `bunker.reidar.tech`. Caddy is the only public entry point; Compose
binds port 8000 to loopback. The named data volume is explicitly
`bunkerkartet-data`, so backup commands do not depend on the Compose project
directory. The example also enables one-year HSTS; only use it once the
hostname is permanently HTTPS-only.

## SQLite backup

Back up the named Compose volume while the service is stopped. This command
fails if the expected volume does not exist or the archive is not created.

```bash
set -eu
cd /opt/bunkerkartet
VOLUME=bunkerkartet-data
BACKUP_DIR=/srv/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$BACKUP_DIR/bunkerkartet-$STAMP.tar.gz"

docker volume inspect "$VOLUME" >/dev/null
mkdir -p "$BACKUP_DIR"
trap 'docker compose start bunkerkartet' EXIT
docker compose stop bunkerkartet
docker run --rm \
  -v "$VOLUME:/data:ro" \
  -v "$BACKUP_DIR:/backup" \
  alpine:3.20 sh -c "tar -czf /backup/$(basename "$BACKUP") -C /data ."
test -s "$BACKUP"
docker compose start bunkerkartet
trap - EXIT
```

Store the archive outside the application directory and retain it according to
the normal server backup policy.

## Restore preflight

Use a separate staging volume or directory for every restore exercise. Stop the
writer before handling SQLite files; `bunkerkartet.sqlite3`, its `-wal`, and
its `-shm` file are one consistency set. Never copy only the main file from a
live WAL-mode database.

Before stopping the service, reject archives containing absolute paths,
`..` path components, symlinks, hardlinks, or any member other than the three
SQLite files. This inspect-only check does not extract anything:

```bash
python3 - "$ARCHIVE" <<'PY'
import sys
import tarfile

allowed = {"bunkerkartet.sqlite3", "bunkerkartet.sqlite3-wal", "bunkerkartet.sqlite3-shm"}
seen = set()
root_seen = False
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    for member in members:
        if member.name in {".", "./"}:
            if root_seen or not member.isdir():
                raise SystemExit("archive rejected")
            root_seen = True
            continue
        name = member.name[2:] if member.name.startswith("./") else member.name
        if (member.name.startswith("/") or ".." in name.split("/")
                or name not in allowed or not member.isreg()
                or member.issym() or member.islnk() or name in seen):
            raise SystemExit("archive rejected")
        seen.add(name)
    if "bunkerkartet.sqlite3" not in seen:
        raise SystemExit("archive rejected")
PY
```

Extract into staging, then run the repository's read-only verifier against the
staged database before replacing the volume. It must report the expected
schema version, `integrity_check=ok`, an empty foreign-key check, and table
counts. Keep the pre-restore archive until the application health check passes;
that archive is the rollback point. Record the restore timestamp, expected
version, archive hash, and whether any writes after the backup are outside the
RPO.

## SQLite restore

Restore only during a maintenance window. Pass the archive path, exact
deployed commit SHA, and schema version as arguments. The script validates the
archive before stopping writes, confirms the stable volume, creates a
pre-restore backup, extracts into a separate staging volume, verifies the
staged database, and only then replaces the current volume contents. Any
failed precondition exits before replacement and the trap starts the service.

```bash
set -eu
cd /opt/bunkerkartet
ARCHIVE="${1:?usage: $0 /srv/backups/file.tar.gz COMMIT_SHA SCHEMA_VERSION}"
EXPECTED_VERSION="${2:?usage: $0 /srv/backups/file.tar.gz COMMIT_SHA SCHEMA_VERSION}"
EXPECTED_SCHEMA_VERSION="${3:?usage: $0 /srv/backups/file.tar.gz COMMIT_SHA SCHEMA_VERSION}"
VOLUME=bunkerkartet-data
BACKUP_DIR=/srv/backups
ARCHIVE_DIR=$(cd "$(dirname "$ARCHIVE")" && pwd)
ARCHIVE_NAME=$(basename "$ARCHIVE")
PRE_BACKUP="$BACKUP_DIR/bunkerkartet-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
STAGING_VOLUME="bunkerkartet-restore-staging-$(date -u +%Y%m%dT%H%M%SZ)"

test -s "$ARCHIVE"
docker volume inspect "$VOLUME" >/dev/null
python3 scripts/verify_restore_archive.py --archive "$ARCHIVE"
mkdir -p "$BACKUP_DIR"
docker volume create "$STAGING_VOLUME" >/dev/null
trap 'rm -rf "${CHECK_DIR:-}"; docker volume rm "$STAGING_VOLUME" >/dev/null 2>&1 || true; docker compose start bunkerkartet' EXIT
docker compose stop bunkerkartet

docker run --rm \
  -v "$VOLUME:/data:ro" \
  -v "$BACKUP_DIR:/backup" \
  alpine:3.20 sh -c "tar -czf /backup/$(basename "$PRE_BACKUP") -C /data ."
test -s "$PRE_BACKUP"

docker run --rm -e ARCHIVE_NAME="$ARCHIVE_NAME" \
  -v "$STAGING_VOLUME:/stage" \
  -v "$ARCHIVE_DIR:/archive:ro" \
  alpine:3.20 sh -c '
    set -eu
    tar -xzf "/archive/$ARCHIVE_NAME" -C /stage
    test -s /stage/bunkerkartet.sqlite3
  '

CHECK_DIR="$BACKUP_DIR/.bunkerkartet-restore-check-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$CHECK_DIR"
docker run --rm \
  -v "$STAGING_VOLUME:/stage:ro" \
  -v "$CHECK_DIR:/check" \
  alpine:3.20 sh -c 'cp /stage/bunkerkartet.sqlite3* /check/'
python3 scripts/verify_database.py \
  --database "$CHECK_DIR/bunkerkartet.sqlite3" \
  --expected-version "$EXPECTED_SCHEMA_VERSION"
rm -rf "$CHECK_DIR"

docker run --rm \
  -v "$VOLUME:/data" \
  -v "$STAGING_VOLUME:/stage:ro" \
  alpine:3.20 sh -c '
    set -eu
    find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    for name in bunkerkartet.sqlite3 bunkerkartet.sqlite3-wal bunkerkartet.sqlite3-shm; do
      if [ -e "/stage/$name" ]; then cp "/stage/$name" "/data/$name"; fi
    done
  '

docker volume rm "$STAGING_VOLUME" >/dev/null
docker compose start bunkerkartet
trap - EXIT
health=$(curl --fail --silent http://127.0.0.1:8000/api/health)
printf '%s' "$health" | grep -Fq '"status":"healthy"'
printf '%s' "$health" | grep -Fq "\"version\":\"$EXPECTED_VERSION\""
```

The restore command intentionally stops writes and must never be run against a
live app without the pre-restore backup step.
