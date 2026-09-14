from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_container_and_compose_configs_keep_the_runtime_restricted():
    dockerfile = (ROOT / "Dockerfile").read_text()
    for compose_path in (ROOT / "docker-compose.yml", ROOT / "deploy/templates/docker-compose.yml.j2"):
        compose = compose_path.read_text()
        assert "read_only: true" in compose
        assert "no-new-privileges:true" in compose
        assert "cap_drop:" in compose
        assert "- ALL" in compose
        assert "tmpfs:" in compose
    assert "FROM python:3.12-slim@sha256:" in dockerfile


def test_ci_checks_frontend_syntax():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "node --check app/static/app.js" in workflow
