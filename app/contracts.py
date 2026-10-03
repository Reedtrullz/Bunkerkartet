"""Stable client contracts; application build identity is exposed separately."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

API_CONTRACT_VERSION = '1.0'

class SiteSummaryResponse(BaseModel):
    model_config=ConfigDict(extra='allow',allow_inf_nan=False)
    id: int = Field(gt=0)
    external_key: str
    name: str
    site_kind: str
    revision: int = Field(gt=0)
    status: Literal['candidate','approximate','likely','trusted','field-verified','destroyed-or-filled','rejected']
    access: Literal['public','private','restricted','unknown','permission_required','dangerous','unsafe']
    latitude: float | None
    longitude: float | None
    uncertainty_m: float | None
    warnings: list[str] | None
    route_eligible: bool
    route_blocking_reason: str | None
    data_status: Literal['valid','unavailable']
    content_status: Literal['seed','override','absent','unavailable']

class SourceReadingResponse(BaseModel):
    model_config=ConfigDict(extra='allow')
    url: str | None
    title: str | None
    source_type: str | None
    excerpt: str | None
    evidence_id: int | None
    citation_status: Literal['recorded_reading','historical_metadata_unknown']
    rights_status: Literal['unknown','permission_recorded','restricted']

class SiteDetailResponse(SiteSummaryResponse):
    enrichment: dict | None
    content_document: dict | None
    sources: list[SourceReadingResponse]
    field_observations: list[dict]
    relations: list[dict]

class ImportReceiptResponse(BaseModel):
    model_config=ConfigDict(extra='allow')
    id: int = Field(gt=0)
    batch_id: str
    schema_version: Literal['1.0','1.1']
    payload_hash: str | None
    records: list[dict]
    evidence_items: int = Field(ge=0)
    source_links: int = Field(ge=0)
    receipt_status: Literal['recorded','legacy_hash_unavailable']
