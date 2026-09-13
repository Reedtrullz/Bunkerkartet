# Deployment

This repository prepares deployment but does not deploy anything. The intended
runtime is a Docker Compose service on the VPS, with Caddy terminating public
TLS and proxying to the app on `127.0.0.1:8000`.

## Build and publish

GitHub Actions runs tests and builds the image for pull requests and pushes to
`main`. Only a push to the protected `main` branch publishes to GHCR. The
published image receives the full commit SHA tag and the workflow summary
records its digest. Deploy both values; the Compose service runs by digest.

```text
ghcr.io/reedtrullz/bunkerkartet@sha256:0123456789abcdef...
```

Do not use `latest` or a mutable branch tag. A commit SHA tag is used to find
the published artifact; the digest is the enforced runtime identity.

## Ansible preparation

Copy `deploy/inventory.example.yml` to an ignored `deploy/inventory.yml`, then
replace the placeholder host, commit SHA, and published image digest. Provide
the server's GHCR username and read-only package token through an Ansible Vault
file or another external secret source. Do not put registry credentials in the
application `.env` or in Git.

```bash
ansible-playbook -i deploy/inventory.yml deploy/site.yml \
  -e @/path/to/bunkerkartet-registry-vault.yml
```

The playbook requires a 40-character commit SHA, a `sha256:` digest, and
registry credentials. It logs in, pulls the SHA tag, verifies that it resolves
to the expected digest, writes the digest-pinned Compose file, and checks both
`/api/health` and its reported version. It does not configure DNS, Caddy, or
credentials.

## Caddy

Copy the relevant block from `deploy/Caddyfile.example` into the host Caddy
configuration, replace the hostname, and reload Caddy using the host's normal
service manager. Caddy is the only public entry point; Compose binds port 8000
to loopback. The named data volume is explicitly `bunkerkartet-data`, so backup
commands do not depend on the Compose project directory.

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

## SQLite restore

Restore only during a maintenance window. Pass the archive path and the exact
deployed commit SHA as arguments. The script first checks the archive, confirms
the stable volume, stops writes, creates a pre-restore backup, verifies the
archive contains a database, extracts to a staging directory, and only then
replaces the current volume contents. Any failed precondition exits before the
replacement step and the trap starts the service again.

```bash
set -eu
cd /opt/bunkerkartet
ARCHIVE="${1:?usage: $0 /srv/backups/file.tar.gz COMMIT_SHA}"
EXPECTED_VERSION="${2:?usage: $0 /srv/backups/file.tar.gz COMMIT_SHA}"
VOLUME=bunkerkartet-data
BACKUP_DIR=/srv/backups
ARCHIVE_DIR=$(cd "$(dirname "$ARCHIVE")" && pwd)
ARCHIVE_NAME=$(basename "$ARCHIVE")
PRE_BACKUP="$BACKUP_DIR/bunkerkartet-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"

test -s "$ARCHIVE"
docker volume inspect "$VOLUME" >/dev/null
mkdir -p "$BACKUP_DIR"
trap 'docker compose start bunkerkartet' EXIT
docker compose stop bunkerkartet

docker run --rm \
  -v "$VOLUME:/data:ro" \
  -v "$BACKUP_DIR:/backup" \
  alpine:3.20 sh -c "tar -czf /backup/$(basename "$PRE_BACKUP") -C /data ."
test -s "$PRE_BACKUP"

docker run --rm -e ARCHIVE_NAME="$ARCHIVE_NAME" \
  -v "$ARCHIVE_DIR:/archive:ro" \
  alpine:3.20 sh -c 'tar -tzf "/archive/$ARCHIVE_NAME" | grep -Eq "(^|\\./)bunkerkartet.sqlite3$"'

docker run --rm -e ARCHIVE_NAME="$ARCHIVE_NAME" \
  -v "$VOLUME:/data" \
  -v "$ARCHIVE_DIR:/archive:ro" \
  alpine:3.20 sh -c '
    set -eu
    rm -rf /data/.restore-staging
    mkdir /data/.restore-staging
    tar -xzf "/archive/$ARCHIVE_NAME" -C /data/.restore-staging
    test -s /data/.restore-staging/bunkerkartet.sqlite3
    find /data -mindepth 1 -maxdepth 1 ! -name .restore-staging -exec rm -rf {} +
    find /data/.restore-staging -mindepth 1 -maxdepth 1 -exec mv {} /data/ \;
    rmdir /data/.restore-staging
  '

docker compose start bunkerkartet
trap - EXIT
health=$(curl --fail --silent http://127.0.0.1:8000/api/health)
printf '%s' "$health" | grep -Fq '"status":"healthy"'
printf '%s' "$health" | grep -Fq "\"version\":\"$EXPECTED_VERSION\""
```

The restore command intentionally stops writes and must never be run against a
live app without the pre-restore backup step.
