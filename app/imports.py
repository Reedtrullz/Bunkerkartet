from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Geometry(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SourceEvidence(StrictModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    source_type: str = Field(min_length=1, max_length=100)
    excerpt: str = Field(min_length=1, max_length=2000)
    publication_date: date | None = None
    access_date: date | None = None

    @field_validator("publication_date", "access_date", mode="before")
    @classmethod
    def parse_iso_date(cls, value: date | str | None) -> date | None:
        if value is None or isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("date must be ISO 8601 YYYY-MM-DD") from exc
        raise ValueError("date must be an ISO 8601 YYYY-MM-DD string")


class ImportRecord(StrictModel):
    external_key: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=500)
    site_kind: str = Field(min_length=1, max_length=100)
    geometry: Geometry | None = None
    precision: Literal["exact", "approximate", "unknown"]
    uncertainty_m: float | None = Field(default=None, ge=0)
    location_basis: Literal[
        "explicit_coordinate",
        "address",
        "map_reference",
        "landmark_description",
        "llm_inference",
    ]
    status: Literal["candidate"]
    access: Literal["unknown", "public", "restricted", "private", "permission_required", "dangerous"]
    sources: list[SourceEvidence] = Field(min_length=1)
    confidence: Literal["high", "medium", "low", "unknown"] | None = None
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    condition: str | None = Field(default=None, max_length=1000)
    warnings: list[str] = Field(default_factory=list)
    related_site_keys: list[str] = Field(default_factory=list)

    @field_validator("name", "site_kind")
    @classmethod
    def reject_snublesteiner(cls, value: str) -> str:
        if "snublestein" in value.casefold():
            raise ValueError("snublestein records are excluded")
        return value

    @model_validator(mode="after")
    def validate_geometry(self) -> "ImportRecord":
        if self.geometry is None and self.precision != "unknown":
            raise ValueError("geometry may be null only when precision is unknown")
        if self.geometry is not None and self.uncertainty_m is None:
            raise ValueError("uncertainty_m is required when geometry is present")
        return self


class ImportPackage(StrictModel):
    schema_version: Literal["1.0"]
    batch_id: str = Field(min_length=1, max_length=200)
    generated_at: datetime
    records: list[ImportRecord] = Field(min_length=1)

    @field_validator("generated_at", mode="before")
    @classmethod
    def parse_and_require_timezone(cls, value: datetime | str) -> datetime:
        if not isinstance(value, (datetime, str)):
            raise ValueError("generated_at must be an ISO 8601 datetime")
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("generated_at must be an ISO 8601 datetime") from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value


def validate_import_package(payload: object) -> ImportPackage:
    return ImportPackage.model_validate(payload)
