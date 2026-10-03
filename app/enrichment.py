from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.imports import validate_reference_url
from app.json_input import decode_json_strict


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    accessed_at: date | None = None

    @field_validator("accessed_at", mode="before")
    @classmethod
    def parse_accessed_at(cls, value: object) -> object:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("url", mode="before")
    @classmethod
    def reject_credential_urls(cls, value: str) -> str:
        return validate_reference_url(value)


class ResearchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=100)
    section: Literal["about", "current", "visit_summary", "physical_access", "access_rules", "uncertainty"]
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
    research_state: Literal["curated", "researched_pending", "identity_review"] = "curated"
    reviewed_at: date
    sources: list[ResearchSource] = Field(min_length=1, max_length=50)
    claims: list[ResearchClaim] = Field(min_length=1, max_length=30)

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def parse_reviewed_at(cls, value: object) -> object:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_claim_sources(self) -> "ResearchSite":
        if any(not value.strip() for value in (self.external_key, self.display_name, self.kind_label)):
            raise ValueError("research identity fields cannot be blank")
        if any(not source.id.strip() or not source.title.strip() for source in self.sources):
            raise ValueError("source identity fields cannot be blank")
        if any(not claim.text.strip() for claim in self.claims):
            raise ValueError("claim text cannot be blank")
        summaries = [claim for claim in self.claims if claim.section == "visit_summary"]
        if len(summaries) > 1 or any(len(claim.text) > 400 for claim in summaries):
            raise ValueError("one visit summary of at most 400 characters is allowed")
        source_ids = {source.id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("site sources must have unique ids")
        if any(not claim.id.strip() for claim in self.claims):
            raise ValueError("site claim ids cannot be blank")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("site claims must have unique ids")
        for claim in self.claims:
            if len(set(claim.source_ids)) != len(claim.source_ids):
                raise ValueError("claim source references must be unique")
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
        "id": claim.id,
        "section": claim.section,
        "text": claim.text,
        "certainty": certainty,
        "source_ids": list(claim.source_ids),
        "sources": [sources[source_id].model_dump(mode="json") for source_id in claim.source_ids],
    }


def load_research_site(value: str | dict, expected_external_key: str | None = None) -> ResearchSite:
    raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    document = decode_json_strict(raw, max_bytes=2 * 1024 * 1024)
    site = ResearchSite.model_validate_json(json.dumps(document))
    if expected_external_key is not None and site.external_key != expected_external_key:
        raise ValueError("stored content identity does not match site")
    return site


def load_site_documents(path: Path = ENRICHMENT_PATH) -> dict[str, ResearchSite]:
    try:
        raw = decode_json_strict(path.read_bytes(), max_bytes=2 * 1024 * 1024)
        document = ResearchDocument.model_validate_json(json.dumps(raw))
        if len({site.external_key for site in document.sites}) != len(document.sites):
            raise ValueError("site enrichment keys must be unique")
    except (OSError, ValueError) as error:
        raise RuntimeError("site enrichment overlay is invalid") from error
    return {site.external_key: site for site in document.sites}


def research_site_payload(site: ResearchSite) -> dict[str, object]:
    sources = {source.id: source for source in site.sources}
    claims = {
        section: [_claim_payload(claim, sources) for claim in site.claims if claim.section == section]
        for section in ("about", "current", "visit_summary", "physical_access", "access_rules", "uncertainty")
    }
    return {
        "sources": [source.model_dump(mode="json") for source in site.sources],
        "claims": [_claim_payload(claim, sources) for claim in site.claims],
        "display_name": site.display_name, "kind_label": site.kind_label,
        "research_state": site.research_state, "reviewed_at": site.reviewed_at.isoformat(),
        "about": claims["about"], "present_day": claims["current"],
        "visit_summary": claims["visit_summary"], "uncertainty": claims["uncertainty"],
        "visit_access": {"physical_access": claims["physical_access"], "access_rules": claims["access_rules"]},
    }


def load_site_enrichment(path: Path = ENRICHMENT_PATH) -> dict[str, dict[str, object]]:
    return {key: research_site_payload(site) for key, site in load_site_documents(path).items()}
