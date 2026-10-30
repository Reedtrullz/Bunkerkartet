from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Depends, HTTPException, Query
    from app.main import (
        CONFIDENCE_LEVELS, SITE_ACCESSES, SITE_STATUSES, SiteSummaryResponse,
        _source_rows,
    )


def install_before(app, database, effective_enrichment, read_guard, site_summary):
    @app.get("/api/sites", dependencies=[Depends(read_guard)], response_model=list[SiteSummaryResponse])
    def list_sites(
        status: str | None = Query(default=None),
        site_kind: str | None = Query(default=None, max_length=100),
        q: str | None = Query(default=None, max_length=200),
        access: str | None = Query(default=None),
        confidence: str | None = Query(default=None),
        include_rejected: bool = Query(default=False),
    ) -> list[dict[str, object]]:
        conditions = ["merged_into_id IS NULL"]
        values: list[object] = []
        if status:
            if status not in SITE_STATUSES:
                raise HTTPException(400, "invalid site status")
            conditions.append("status = ?")
            values.append(status)
        elif not include_rejected:
            conditions.append("status <> 'rejected'")
        if access:
            if access not in SITE_ACCESSES:
                raise HTTPException(400, "invalid site access")
            conditions.append("access = ?")
            values.append(access)
        if confidence:
            if confidence not in CONFIDENCE_LEVELS:
                raise HTTPException(400, "invalid site confidence")
            conditions.append("COALESCE(confidence, 'unknown') = ?")
            values.append(confidence)
        query = f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY name, id"
        with database.connect() as connection:
            from app.catalogue_quality import batch_observation_points
            rows = connection.execute(query, values).fetchall()
            points = batch_observation_points(connection, [int(row["id"]) for row in rows])
            result = []
            for row in rows:
                if site_kind and site_kind.strip().casefold() not in row["site_kind"].casefold(): continue
                if q and q.strip():
                    enrichment = effective_enrichment(row) or {}
                    texts = [str(row[field] or "") for field in ("name", "site_kind", "short_rationale", "observed_location_text", "condition")]
                    texts.extend(str(enrichment.get(field, "")) for field in ("display_name", "kind_label"))
                    texts.extend(claim["text"] for claim in enrichment.get("claims", []))
                    for reading in _source_rows(connection, int(row["id"])):
                        texts.extend(str(reading.get(field) or "") for field in ("title", "registry_title", "excerpt"))
                    if not any(q.strip().casefold() in value.casefold() for value in texts): continue
                result.append(site_summary(connection, row, observation_points=points[int(row["id"])]))
            return result

    return list_sites
