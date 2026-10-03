"""Explicit three-way review of seed changes against stable curator identities."""
from copy import deepcopy
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import Field
from app.db import canonical_payload_hash,decode_stored_json,dump_json,now_iso
from app.enrichment import load_research_site
from app.research import ResearchInput


def flatten(document):
    result={key:value for key,value in document.items() if key not in {'claims','sources'}}
    for collection in ('claims','sources'):
        result.update({collection+':'+item['id']:item for item in document.get(collection,[])})
    return result


def reconciliation_preview(base,current,local,revision):
    maps=[flatten(document) for document in (base,current,local)];items=[]
    for key in sorted(maps[0].keys()|maps[1].keys()|maps[2].keys()):
        values=[{'present':key in value,'value':value.get(key)} for value in maps]
        if values[0]==values[1]==values[2]:continue
        items.append({'key':key,'base':values[0],'current':values[1],'local':values[2],'conflict':values[0]!=values[1] and values[0]!=values[2] and values[1]!=values[2]})
    effect={'base_seed_hash':canonical_payload_hash(base),'current_seed_hash':canonical_payload_hash(current),'local_hash':canonical_payload_hash(local),'revision':revision,'external_key':local['external_key'],'base':base,'items':items}
    effect['preview_hash']=canonical_payload_hash(effect)
    return effect


def resolve_reconciliation(effect,choices):
    if set(choices)!=set(item['key'] for item in effect['items']):raise ValueError('choose each proposed item explicitly')
    result=flatten(effect['base'])
    for item in effect['items']:
        choice=choices[item['key']]
        if choice not in ('base','current','local'):raise ValueError('invalid reconciliation choice')
        selected=item[choice]
        if selected['present']:result[item['key']]=deepcopy(selected['value'])
        else:result.pop(item['key'],None)
    document={key:value for key,value in result.items() if not key.startswith(('claims:','sources:'))}
    for collection in ('claims','sources'):
        document[collection]=[value for key,value in result.items() if key.startswith(collection+':')]
    if document.get('external_key')!=effect['external_key']:raise ValueError('reconciliation cannot change identity')
    # Deletions are explicit retirement, never positional reassignment.
    removed={item['id'] for item in effect['base'].get('claims',[])}-{item['id'] for item in document['claims']}
    retired=set(document.get('retired_claim_ids',[]))|removed
    if retired:document['retired_claim_ids']=sorted(retired)
    return load_research_site(document,effect['external_key']).model_dump(mode='json',exclude_none=True)


class ReconciliationCommit(ResearchInput):
    expected_revision:int=Field(gt=0)
    preview_hash:str=Field(min_length=64,max_length=64)
    reason:str=Field(min_length=1,max_length=2000)
    choices:dict[str,Literal['base','current','local']]=Field(max_length=200)


def install_reconciliation_routes(app,database,guard,documents,effective_content,site_detail):
    def effect(c,site_id):
        row=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
        if row is None:raise HTTPException(404,'site not found')
        if row['merged_into_id'] is not None:raise HTTPException(409,'merged identity is immutable')
        local,status=effective_content(row);current=documents.get(row['external_key'])
        if local is None or current is None:raise HTTPException(409,'seed or local content unavailable')
        event=c.execute("SELECT payload_json FROM site_events WHERE site_id=? AND event_type IN ('content_edit','seed_reconciliation') ORDER BY id DESC LIMIT 1",(site_id,)).fetchone()
        payload,stored_status=decode_stored_json(event[0],'object') if event else ({},'absent_legacy')
        base=payload.get('seed_base') if payload else None
        if base is None:
            first=c.execute("SELECT payload_json FROM site_events WHERE site_id=? AND event_type='content_edit' ORDER BY id LIMIT 1",(site_id,)).fetchone()
            old,old_status=decode_stored_json(first[0],'object') if first else ({},'absent_legacy')
            base=old.get('before') if old else None
        if base is None and row['content_json'] is not None:raise HTTPException(409,'legacy override has no recorded seed base; review original history first')
        base=base or current.model_dump(mode='json',exclude_none=True)
        return row,reconciliation_preview(base,current.model_dump(mode='json',exclude_none=True),local.model_dump(mode='json',exclude_none=True),row['revision'])
    @app.get('/api/sites/{site_id}/seed-reconciliation',dependencies=[Depends(guard)])
    def preview(site_id:int):
        with database.connect() as c:return effect(c,site_id)[1]
    @app.post('/api/sites/{site_id}/seed-reconciliation',dependencies=[Depends(guard)])
    def commit(site_id:int,body:ReconciliationCommit):
        with database.connect() as c:
            c.execute('BEGIN IMMEDIATE');row,receipt=effect(c,site_id)
            if row['revision']!=body.expected_revision or receipt['preview_hash']!=body.preview_hash:raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'seed or local content changed; review again'})
            try:after=resolve_reconciliation(receipt,body.choices)
            except ValueError as error:raise HTTPException(422,'reconciled content requires valid sources and claim identities') from error
            before=effective_content(row)[0].model_dump(mode='json',exclude_none=True)
            c.execute('UPDATE sites SET content_json=?,revision=revision+1,updated_at=? WHERE id=?',(dump_json(after),now_iso(),site_id))
            payload={'before':before,'after':after,'reason':body.reason,'choices':body.choices,'base_seed_hash':receipt['base_seed_hash'],'current_seed_hash':receipt['current_seed_hash'],'preview_hash':receipt['preview_hash'],'seed_base':documents[row['external_key']].model_dump(mode='json',exclude_none=True)}
            c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'seed_reconciliation',?,?)",(site_id,dump_json(payload),now_iso()))
            return {'receipt':payload,'site':site_detail(c,c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone())}
