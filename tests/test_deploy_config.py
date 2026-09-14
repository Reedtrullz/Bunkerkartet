from pathlib import Path
import re


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
    action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflow)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_refs)

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"FROM python:3\.12-slim@sha256:[0-9a-f]{64}", dockerfile)


def test_dependabot_tracks_runtime_container_and_actions():
    config = (ROOT / ".github/dependabot.yml").read_text()

    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: docker" in config
    assert "package-ecosystem: github-actions" in config
