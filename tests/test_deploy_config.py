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
        assert "/api/ready" in compose
    assert "FROM python:3.12-slim@sha256:" in dockerfile


def test_ci_checks_frontend_syntax():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "node --check app/static/app.js" in workflow


def test_ci_actions_are_commit_pinned():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflow)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_refs)
    assert "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc" in workflow

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"FROM python:3\.12-slim@sha256:[0-9a-f]{64}", dockerfile)


def test_dependabot_tracks_runtime_container_and_actions():
    config = (ROOT / ".github/dependabot.yml").read_text()

    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: docker" in config
    assert "package-ecosystem: github-actions" in config


def test_dependabot_cannot_move_the_container_to_a_new_python_major():
    config = (ROOT / ".github/dependabot.yml").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "dependency-name: python" in config
    assert "version-update:semver-major" in config
    assert 'python-version: "3.12"' in workflow
    assert "FROM python:3.12-slim@sha256:" in dockerfile


def test_release_governance_draft_is_explicitly_inactive():
    import json

    governance = json.loads((ROOT / "docs/operations/release-governance.json").read_text())
    assert governance["activation_authorized"] is False
    assert governance["owner_decision"] == "pending"
    assert governance["proposed_policy"]["required_status_checks"] == ["test"]
    assert governance["proposed_policy"]["bypass_actors"] == []


def test_ci_retains_dependency_receipt_and_runs_nonroot_container_smoke():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "pip freeze" in workflow
    assert "tests/test_container_smoke.py" in workflow
    assert "BUNKERKARTET_SMOKE_IMAGE" in workflow


def test_deploy_preflights_private_auth_backup_and_selected_port():
    playbook = (ROOT / "deploy/site.yml").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    template = (ROOT / "deploy/templates/docker-compose.yml.j2").read_text()

    assert "ADMIN_TOKEN" in playbook
    assert "bunkerkartet_backup_archive" in playbook
    assert "bunkerkartet_backup_sha256" in playbook
    assert "/api/ready" in playbook
    assert "/api/version" in playbook
    assert "${APP_PORT:-8000}" in compose
    assert "bunkerkartet_app_port" in template


def test_deployment_schema_example_tracks_the_reviewed_release():
    inventory = (ROOT / "deploy/inventory.example.yml").read_text()
    for key in (
        "bunkerkartet_expected_schema_version",
        "bunkerkartet_backup_schema_version",
        "bunkerkartet_rollback_schema_version",
    ):
        expected = "CURRENT" if key == "bunkerkartet_expected_schema_version" else "PREVIOUS"
        assert f"{key}: REPLACE_WITH_{expected}_SCHEMA_VERSION" in inventory


def test_release_receipt_binds_backup_schema_readiness_and_rollback(tmp_path):
    from app.db import CURRENT_SCHEMA_VERSION, Database
    from scripts.backup_database import create_backup
    from scripts.release_receipt import build_release_receipt, main, validate_release_receipt
    import json

    database = Database(tmp_path / "source.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) "
            "VALUES ('synthetic:receipt', 'Synthetic receipt site', 'bunker', 'unknown', 'explicit_coordinate', '2026-10-03', '2026-10-03')"
        )
    archive = tmp_path / "backup.tar.gz"
    backup = create_backup(database.path, archive)

    receipt = build_release_receipt(
        commit_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        database_schema_version=CURRENT_SCHEMA_VERSION,
        backup=backup,
        selected_port=8123,
        readiness={"status": "ready", "database": "ready", "authentication": "configured"},
        version={"version": "a" * 40},
        rollback={
            "image_digest": "sha256:" + "c" * 64,
            "database_schema_version": CURRENT_SCHEMA_VERSION,
            "data_volume": "bunkerkartet-data",
            "backup_sha256": backup["sha256"],
        },
    )

    validate_release_receipt(receipt)
    assert receipt["commit_sha"] == "a" * 40
    assert receipt["image_digest"] == "sha256:" + "b" * 64
    assert receipt["backup"]["sha256"] == backup["sha256"]
    assert receipt["database_schema_version"] == CURRENT_SCHEMA_VERSION
    assert receipt["selected_port"] == 8123
    assert receipt["readiness"]["status"] == "ready"
    assert receipt["version"] == {"version": "a" * 40}
    assert receipt["rollback"]["image_digest"] == "sha256:" + "c" * 64
    assert receipt["rollback"]["data_volume"] == "bunkerkartet-data"

    readiness_path = tmp_path / "readiness.json"
    version_path = tmp_path / "version.json"
    output_path = tmp_path / "release-receipt.json"
    readiness_path.write_text(json.dumps({"status": "ready", "database": "ready", "authentication": "configured", "secret": "omit-me"}))
    version_path.write_text(json.dumps({"version": "a" * 40, "secret": "omit-me"}))
    arguments = [
        "build", "--commit-sha", "a" * 40, "--image-digest", "sha256:" + "b" * 64,
        "--database-schema-version", str(CURRENT_SCHEMA_VERSION), "--backup-archive", str(archive),
        "--backup-receipt", str(archive) + ".receipt.json", "--selected-port", "8123",
        "--readiness-json", str(readiness_path), "--version-json", str(version_path),
        "--rollback-image-digest", "sha256:" + "c" * 64, "--rollback-schema-version", str(CURRENT_SCHEMA_VERSION),
        "--rollback-data-volume", "bunkerkartet-data", "--rollback-backup-sha256", backup["sha256"],
        "--output", str(output_path),
    ]
    assert main(arguments) == 0
    assert main(arguments) == 0
    assert main(["verify", str(output_path)]) == 0
    persisted = output_path.read_text()
    assert 'omit-me' not in persisted
    assert output_path.stat().st_mode & 0o077 == 0


def test_release_receipt_rejects_wrong_version_and_unready_service(tmp_path):
    from app.db import CURRENT_SCHEMA_VERSION, Database
    from scripts.backup_database import create_backup
    from scripts.release_receipt import ReleaseReceiptError, build_release_receipt
    import pytest

    database = Database(tmp_path / "source.sqlite3")
    database.initialize()
    archive = tmp_path / "backup.tar.gz"
    backup = create_backup(database.path, archive)
    arguments = {
        "commit_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "database_schema_version": CURRENT_SCHEMA_VERSION,
        "backup": backup,
        "selected_port": 8000,
        "readiness": {"status": "ready", "database": "ready", "authentication": "configured"},
        "version": {"version": "a" * 40},
        "rollback": {
            "image_digest": "sha256:" + "c" * 64,
            "database_schema_version": CURRENT_SCHEMA_VERSION,
            "data_volume": "bunkerkartet-data",
            "backup_sha256": backup["sha256"],
        },
    }

    with pytest.raises(ReleaseReceiptError, match="version"):
        build_release_receipt(**{**arguments, "version": {"version": "d" * 40}})
    with pytest.raises(ReleaseReceiptError, match="readiness"):
        build_release_receipt(**{**arguments, "readiness": {"status": "not_ready", "database": "ready", "authentication": "configured"}})
