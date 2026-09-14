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
        assert "healthcheck:" in compose
        assert "/api/health" in compose
    assert "FROM python:3.12-slim@sha256:" in dockerfile


def test_ci_checks_frontend_syntax():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "node --check app/static/app.js" in workflow


def test_ci_actions_are_commit_pinned():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    for action in (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
        "docker/login-action@dbcb813823bdd20940b903addbd779551569679f",
        "docker/build-push-action@53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    ):
        assert action in workflow


def test_dependabot_tracks_runtime_container_and_actions():
    config = (ROOT / ".github/dependabot.yml").read_text()

    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: docker" in config
    assert "package-ecosystem: github-actions" in config
