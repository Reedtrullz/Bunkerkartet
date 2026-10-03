"""Explicit owner-selected route erasure; no automatic retention schedule."""
from fastapi import Depends, HTTPException, Query
from pydantic import Field, field_validator
from app.research import ResearchInput
from app.db import canonical_payload_hash, dump_json, now_iso, decode_stored_json


class RouteErasure(ResearchInput):
    route_ids: list[int] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)
    preview_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator('route_ids')
    @classmethod
    def unique_positive(cls, values):
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise ValueError('select distinct positive route IDs')
        return sorted(values)


def install_retention_routes(app, database, guard):
    auth = [Depends(guard)]

    def effect(connection, body):
        items = []
        for route_id in body.route_ids:
            row = connection.execute('SELECT * FROM route_plans WHERE id=?', (route_id,)).fetchone()
            if not row: raise HTTPException(404, 'selected route not found')
            if connection.execute('SELECT 1 FROM route_retention_receipts WHERE route_id=?', (route_id,)).fetchone():
                raise HTTPException(409, 'selected route has already been erased')
            visits = [int(item[0]) for item in connection.execute(
                "SELECT id FROM observation_visits WHERE json_valid(route_snapshot_json) AND json_extract(route_snapshot_json,'$.id')=? ORDER BY id", (route_id,))]
            items.append({'route_id': route_id, 'name': row['name'], 'created_at': row['created_at'],
                          'content_hash': canonical_payload_hash(dict(row)),
                          'retained_visit_snapshot_ids': visits})
        result = {'items': items, 'reason': body.reason,
                  'erased_fields': ['name','start','waypoints','stops','geometry','gpx','calculation_receipt','metrics'],
                  'retained': ['route ID and erasure receipt', 'site evidence', 'separately retained visit snapshots', 'existing backups and exported files']}
        result['preview_hash'] = canonical_payload_hash(result)
        return result

    @app.post('/api/admin/retention/routes/preview', dependencies=auth)
    def preview(body: RouteErasure):
        with database.connect() as connection: return effect(connection, body)

    @app.get('/api/admin/retention/routes/receipts', dependencies=auth)
    def receipts(limit:int=Query(default=50,ge=1,le=100)):
        with database.connect() as connection:
            return {'items':[decode_stored_json(row[0],'object')[0] for row in connection.execute('SELECT payload_json FROM route_retention_receipts ORDER BY id DESC LIMIT ?',(limit,))]}

    @app.post('/api/admin/retention/routes/commit', dependencies=auth)
    def commit(body: RouteErasure):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            prior = [connection.execute('SELECT payload_json FROM route_retention_receipts WHERE route_id=?',(route_id,)).fetchone() for route_id in body.route_ids]
            if all(prior):
                recorded = [decode_stored_json(row[0],'object')[0] for row in prior]
                if all(item and item.get('preview_hash') == body.preview_hash and item.get('reason') == body.reason for item in recorded):
                    return {'status':'erased','route_ids':body.route_ids,'preview_hash':body.preview_hash,'idempotent':True,'created_at':recorded[0]['created_at']}
            reviewed = effect(connection, body)
            if body.preview_hash != reviewed['preview_hash']: raise HTTPException(409, 'review a fresh exact erasure preview')
            timestamp = now_iso()
            for item in reviewed['items']:
                route_id = item['route_id']
                # Keep the identity row so SQLite cannot reuse a deleted route ID.
                connection.execute("UPDATE route_plans SET name='Erased private route',start_json='{}',waypoints_json='[]',stops_json='[]',geometry_json='[]',gpx_text='',details_json=NULL,route_warnings_json='[]',distance_m=0,duration_s=0 WHERE id=?", (route_id,))
                receipt = {'route_id':route_id,'reason':body.reason,'preview_hash':body.preview_hash,'created_at':timestamp,
                           'retained_visit_snapshot_ids':item['retained_visit_snapshot_ids']}
                connection.execute('INSERT INTO route_retention_receipts(route_id,payload_json,created_at) VALUES(?,?,?)', (route_id,dump_json(receipt),timestamp))
            return {'status':'erased','route_ids':body.route_ids,'preview_hash':body.preview_hash,'created_at':timestamp}
