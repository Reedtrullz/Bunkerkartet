from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.imports import validate_reference_url


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    accessed_at: date | None = None

    @field_validator("url", mode="before")
    @classmethod
    def reject_credential_urls(cls, value: str) -> str:
        return validate_reference_url(value)


class ResearchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=100)
    section: Literal["about", "current", "physical_access", "access_rules", "uncertainty"]
    text: str = Field(min_length=1, max_length=2000)
    certainty: Literal["source_supported", "uncertain", "unknown"]
    source_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_source_for_non_unknown(self) -> "ResearchClaim":
        if self.certainty != "unknown" and not self.source_ids:
            raise ValueError("non-unknown enrichment claims require sources")
        return self


class ResearchSite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    external_key: str = Field(min_length=1, max_length=300)
    display_name: str = Field(min_length=1, max_length=500)
    kind_label: str = Field(min_length=1, max_length=200)
    reviewed_at: date
    sources: list[ResearchSource] = Field(min_length=1, max_length=50)
    claims: list[ResearchClaim] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_claim_sources(self) -> "ResearchSite":
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("site sources must have unique ids")
        for claim in self.claims:
            if not set(claim.source_ids) <= source_ids:
                raise ValueError("claim references an unknown source")
        return self


class ResearchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    sites: list[ResearchSite] = Field(min_length=1, max_length=100)


ENRICHMENT_PATH = Path(__file__).parent / "content" / "site_enrichment.json"


def _claim_payload(claim: ResearchClaim, sources: dict[str, ResearchSource]) -> dict[str, object]:
    certainty = "supported" if claim.certainty == "source_supported" else claim.certainty
    return {
        "text": claim.text,
        "certainty": certainty,
        "sources": [sources[source_id].model_dump(mode="json") for source_id in claim.source_ids],
    }


def load_site_enrichment(path: Path = ENRICHMENT_PATH) -> dict[str, dict[str, object]]:
    try:
        document = ResearchDocument.model_validate_json(path.read_text())
        if len({site.external_key for site in document.sites}) != len(document.sites):
            raise ValueError("site enrichment keys must be unique")
    except (OSError, ValueError) as error:
        raise RuntimeError("site enrichment overlay is invalid") from error

    result: dict[str, dict[str, object]] = {}
    for site in document.sites:
        sources = {source.id: source for source in site.sources}
        claims = {
            section: [_claim_payload(claim, sources) for claim in site.claims if claim.section == section]
            for section in ("about", "current", "physical_access", "access_rules", "uncertainty")
        }
        result[site.external_key] = {
            "display_name": site.display_name,
            "kind_label": site.kind_label,
            "reviewed_at": site.reviewed_at.isoformat(),
            "about": claims["about"],
            "present_day": claims["current"],
            "uncertainty": claims["uncertainty"],
            "visit_access": {
                "physical_access": claims["physical_access"],
                "access_rules": claims["access_rules"],
            },
        }
    return result
