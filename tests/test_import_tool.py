import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_import_validator_reports_fixture_summary():
    result = subprocess.run(
        [sys.executable, "research/validate_import.py", "research/example-import.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": True,
        "batch_id": "example-import-2026-09-13",
        "records": 1,
        "coordinates": 1,
        "source_links": 1,
        "statuses": ["candidate"],
    }


def test_import_validator_does_not_echo_secret_url_value(tmp_path):
    payload = json.loads((ROOT / "research/example-import.json").read_text())
    sentinel = "synthetic-cli-secret"
    payload["records"][0]["sources"][0]["url"] = (
        "https://example.com/map?layer=https%3A%2F%2Fexample.org%2Fwms"
        "%3Fapi_key%3D" + sentinel
    )
    package = tmp_path / "invalid.json"
    package.write_text(json.dumps(payload))

    result = subprocess.run(
        [sys.executable, "research/validate_import.py", str(package)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert sentinel not in result.stderr
