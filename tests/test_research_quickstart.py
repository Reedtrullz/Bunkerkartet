import re
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "README.md",
    ROOT / "research/README.md",
    ROOT / "docs/DEPLOYMENT.md",
    ROOT / "docs/operations/README.md",
]


def _shell_blocks(document):
    return re.findall(r"```(?:sh|bash)\s*\n(.*?)```", document.read_text(), re.DOTALL)


def test_copyable_research_shell_blocks_parse_without_a_production_default():
    snippets = [(path, snippet) for path in DOCS for snippet in _shell_blocks(path)]
    assert snippets
    for path, snippet in snippets:
        result = subprocess.run(["sh", "-n"], input=snippet, text=True, capture_output=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"

    combined = "\n".join(path.read_text() for path in DOCS)
    assert "http://127.0.0.1:8765" in combined
    assert "BUNKERKARTET_URL=https://bunker.reidar.tech" not in combined


def test_synthetic_preview_commit_readback_and_exact_retry(tmp_path):
    token = "synthetic-local-only"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "schema_version": "1.0",
        "batch_id": "synthetic-quickstart-1",
        "generated_at": "2026-10-03T12:00:00Z",
        "records": [{
            "external_key": "synthetic:quickstart",
            "name": "Synthetic quickstart site",
            "site_kind": "bunker",
            "geometry": None,
            "precision": "unknown",
            "uncertainty_m": None,
            "location_basis": "explicit_coordinate",
            "status": "candidate",
            "access": "unknown",
            "sources": [{
                "url": "https://example.invalid/synthetic",
                "title": "Synthetic source",
                "source_type": "fixture",
                "excerpt": "Synthetic fixture only.",
            }],
        }],
    }

    with TestClient(create_app(Settings(data_dir=tmp_path / "disposable-data", admin_token=token, app_version="synthetic"))) as api:
        preview = api.post("/api/admin/imports/preview", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        commit_headers = {**headers, "X-Import-Preview": preview.json()["preview_hash"]}
        first = api.post("/api/admin/imports/commit", headers=commit_headers, json=payload)
        retry = api.post("/api/admin/imports/commit", headers=commit_headers, json=payload)
        sites = api.get("/api/sites", headers=headers)

        assert first.status_code == 200, first.text
        assert retry.status_code == 200, retry.text
        assert first.json()["batch_id"] == retry.json()["batch_id"] == "synthetic-quickstart-1"
        assert sum(site["external_key"] == "synthetic:quickstart" for site in sites.json()) == 1
