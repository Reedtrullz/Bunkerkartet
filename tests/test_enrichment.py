from pathlib import Path

import pytest

from app.enrichment import ENRICHMENT_PATH, load_site_enrichment


def test_overlay_contains_only_the_ten_reviewed_production_keys():
    enrichment = load_site_enrichment()

    assert len(enrichment) == 10
    assert set(enrichment) == {
        "krigskart:413",
        "krigskart:2644",
        "krigskart:2655",
        "krigskart:2659",
        "krigskart:2674",
        "krigskart:536",
        "krigskart:2665",
        "tracesofwar:5554",
        "tracesofwar:3477",
        "tracesofwar:3491",
    }
    assert enrichment["krigskart:413"]["uncertainty"][0]["certainty"] == "uncertain"


def test_overlay_rejects_non_unknown_claim_without_sources(tmp_path: Path):
    invalid = ENRICHMENT_PATH.read_text().replace(
        '"source_ids": [\n            "s1"\n          ]',
        '"source_ids": []',
        1,
    )
    path = tmp_path / "invalid.json"
    path.write_text(invalid)

    with pytest.raises(RuntimeError):
        load_site_enrichment(path)


def test_missing_overlay_has_no_synthetic_history(tmp_path: Path):
    missing = tmp_path / "missing.json"

    with pytest.raises(RuntimeError):
        load_site_enrichment(missing)
