# Task 6 report

## Result

Deployment scaffolding is ready for review. No registry push, VPS connection,
DNS change, Caddy reload, or deployment was performed.

## Included

- `Dockerfile`: Python 3.12 slim image, non-root runtime, `uvicorn app.main:app`.
- `docker-compose.yml`: immutable image-tag inputs, `APP_VERSION`, persistent
  `/app/data`, and loopback-only host binding on port 8000.
- `.github/workflows/ci.yml`: tests and image build on pull requests and
  protected `main` pushes; GHCR publication only on a `main` push, tagged by
  full commit SHA.
- `deploy/site.yml` and `deploy/templates/docker-compose.yml.j2`: Ansible
  preparation/start/health-version verification using a pinned image.
- `deploy/Caddyfile.example`: public TLS reverse-proxy example.
- `docs/DEPLOYMENT.md`: setup boundaries, Caddy guidance, and stopped-write
  SQLite backup/restore commands.

## Checks

- `ansible-playbook --syntax-check -i deploy/inventory.example.yml deploy/site.yml`: passed.
- YAML parsing of Compose, workflow, inventory, and playbook: passed.
- Ansible-rendered Compose template YAML parsing: passed.
- `git diff --check` for task files: passed.
- `docker build --tag bunkerkartet:task6-check .`: passed.
- Bounded container smoke test: passed `/api/health`, `/api/version`, and `/`.
- Native `docker compose` config check: unavailable because this Docker
  installation has no Compose subcommand. The rendered Compose YAML check
  passed instead.
