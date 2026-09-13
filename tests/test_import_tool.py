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
