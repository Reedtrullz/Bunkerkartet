import json
from pathlib import Path

import pytest

from app.enrichment import ENRICHMENT_PATH, load_site_enrichment


def test_overlay_contains_reviewed_production_keys_including_leira_battery():
    enrichment = load_site_enrichment()

    assert len(enrichment) == 11
    assert set(enrichment) == {
        "krigskart:413",
        "krigskart:2644",
        "krigskart:2655",
        "krigskart:2659",
        "krigskart:2674",
        "krigskart:536",
        "krigskart:2665",
        "krigskart:2666",
        "tracesofwar:5554",
        "tracesofwar:3477",
        "tracesofwar:3491",
    }
    assert enrichment["krigskart:413"]["uncertainty"][0]["certainty"] == "uncertain"
    assert all(site["research_state"] == "curated" for site in enrichment.values())
    leira = enrichment["krigskart:2666"]
    assert "fire 10,5 cm SKC/32" in leira["about"][0]["text"]
    assert any("stengt" in claim["text"] for claim in leira["visit_access"]["physical_access"])
    assert any(
        claim["certainty"] == "uncertain"
        for claim in leira["visit_access"]["access_rules"]
    )


def test_overlay_preserves_pending_and_identity_review_states(tmp_path: Path):
    payload = json.loads(ENRICHMENT_PATH.read_text())
    payload["sites"][0]["research_state"] = "researched_pending"
    payload["sites"][1]["research_state"] = "identity_review"
    path = tmp_path / "states.json"
    path.write_text(json.dumps(payload))

    enrichment = load_site_enrichment(path)

    assert enrichment[payload["sites"][0]["external_key"]]["research_state"] == "researched_pending"
    assert enrichment[payload["sites"][1]["external_key"]]["research_state"] == "identity_review"


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
