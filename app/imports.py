from datetime import date, datetime
import math
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


_SECRET_PARAMETER_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "token",
    "password",
    "secret",
    "authorization",
    "signature",
    "sig",
    "x-amz-signature",
    "x-goog-signature",
}


def validate_location(
    latitude: float | None,
    longitude: float | None,
    precision: str,
    uncertainty_m: float | None,
    location_basis: str,
) -> None:
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be recorded together")
    if latitude is None:
        if precision != "unknown" or uncertainty_m is not None:
            raise ValueError("a location without coordinates requires unknown precision and no radius")
        return
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("coordinates must be finite")
    if uncertainty_m is None or not math.isfinite(uncertainty_m) or uncertainty_m < 0:
        raise ValueError("a location with coordinates requires a finite radius")
    if precision == "approximate" and uncertainty_m <= 0:
        raise ValueError("an approximate location requires a positive radius")
    if precision == "exact" and location_basis == "llm_inference":
        raise ValueError("exact precision cannot use llm_inference")


def validate_reference_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("reference URL must be a string")
    if len(value) > 8192:
        raise ValueError("reference URL is too long")

    pending = [(value, 0)]
    seen: set[str] = set()
    while pending:
        current, depth = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            parsed = urlsplit(current)
        except ValueError as exc:
            raise ValueError("reference URL is invalid") from exc
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("reference URL contains credential-like information")
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=200)
        except ValueError as exc:
            raise ValueError("reference URL has too many query fields") from exc
        for name, nested in query:
            if name.casefold() in _SECRET_PARAMETER_NAMES:
                raise ValueError("reference URL contains credential-like information")
            decoded = unquote(nested)
            if decoded != nested or ("://" in nested and "?" in nested):
                if depth >= 4:
                    raise ValueError("reference URL encoding is too deeply nested")
                pending.append((decoded, depth + 1))
        decoded_current = unquote(current)
        if decoded_current != current:
            if depth >= 4:
                raise ValueError("reference URL encoding is too deeply nested")
            pending.append((decoded_current, depth + 1))
    return value


def safe_validation_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "loc": list(error.get("loc", ())),
            "type": error.get("type", "validation_error"),
            "msg": error.get("msg", "Invalid value"),
        }
        for error in errors
    ]


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

    @field_validator("url", mode="before")
    @classmethod
    def reject_credential_urls(cls, value: str) -> str:
        return validate_reference_url(value)

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
    access: Literal["unknown", "public", "restricted", "private", "permission_required", "dangerous", "unsafe"]
    sources: list[SourceEvidence] = Field(min_length=1)
    confidence: Literal["high", "medium", "low", "unknown"] | None = None
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    condition: str | None = Field(default=None, max_length=1000)
    warnings: list[str] = Field(default_factory=list)
    related_site_keys: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name", "site_kind")
    @classmethod
    def reject_snublesteiner(cls, value: str) -> str:
        if "snublestein" in value.casefold():
            raise ValueError("snublestein records are excluded")
        return value

    @model_validator(mode="after")
    def validate_geometry(self) -> "ImportRecord":
        validate_location(
            self.geometry.latitude if self.geometry else None,
            self.geometry.longitude if self.geometry else None,
            self.precision,
            self.uncertainty_m,
            self.location_basis,
        )
        if self.external_key in self.related_site_keys:
            raise ValueError("related_site_keys cannot point to the record itself")
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

    @model_validator(mode="after")
    def reject_duplicate_external_keys(self) -> "ImportPackage":
        seen: set[str] = set()
        for record in self.records:
            if record.external_key in seen:
                raise ValueError(f"duplicate external_key in import package: {record.external_key}")
            seen.add(record.external_key)
        return self


def validate_import_package(payload: object) -> ImportPackage:
    return ImportPackage.model_validate(payload)
