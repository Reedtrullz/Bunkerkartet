# Deployment

This repository prepares deployment but does not deploy anything. The intended
runtime is a Docker Compose service on the VPS, with Caddy terminating public
TLS and proxying to the app on `127.0.0.1:8000`.

## Build and publish

GitHub Actions runs tests and builds the image for pull requests and pushes to
`main`. Only a push to the protected `main` branch publishes to GHCR. The
published tag is the full commit SHA, for example:

```text
ghcr.io/reedtrullz/bunkerkartet:0123456789abcdef...
```

Use that exact tag for deployment. Do not use `latest` or a mutable branch tag.

## Ansible preparation

Copy `deploy/inventory.example.yml`, replace the placeholder host and commit
SHA, and provide the server's private environment file at
`/opt/bunkerkartet/.env`. The file must contain at least `ADMIN_TOKEN`; add
`ORS_API_KEY` when routing is enabled. Keep it outside Git.

```bash
ansible-playbook -i deploy/inventory.yml deploy/site.yml
```

The playbook starts the pinned image and checks both `/api/health` and its
reported `version` before it succeeds. It does not configure DNS, Caddy, or
credentials.

## Caddy

Copy the relevant block from `deploy/Caddyfile.example` into the host Caddy
configuration, replace the hostname, and reload Caddy using the host's normal
service manager. Caddy is the only public entry point; do not publish port
8000 beyond loopback.

## SQLite backup

Back up the named Compose volume while the service is stopped so no writes are
in flight. Store the archive outside the server's application directory.

```bash
cd /opt/bunkerkartet
docker compose stop bunkerkartet
docker run --rm \
  -v bunkerkartet_bunkerkartet-data:/data:ro \
  -v /srv/backups:/backup \
  alpine:3.20 tar -czf /backup/bunkerkartet-$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /data .
docker compose start bunkerkartet
```

Confirm the volume name with `docker volume ls`; Compose may prefix it with the
project directory name.

## SQLite restore

Restore only during a maintenance window. Stop the app, preserve the current
volume, replace its contents from the chosen archive, then start Compose and
verify the reported version and health.

```bash
cd /opt/bunkerkartet
docker compose stop bunkerkartet
docker run --rm \
  -v bunkerkartet_bunkerkartet-data:/data \
  -v /srv/backups:/backup \
  alpine:3.20 sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* && tar -xzf /backup/CHOSEN_BACKUP.tar.gz -C /data'
docker compose start bunkerkartet
curl --fail http://127.0.0.1:8000/api/health
```

Take a fresh backup of the existing volume before restoring. The restore
command intentionally stops writes; it must not be run against a live app.
