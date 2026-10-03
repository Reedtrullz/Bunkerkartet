"""Private currentness decisions and bounded, compensating ordinary-text history."""
from datetime import date
from typing import Literal
from fastapi import Depends,HTTPException,Query
from pydantic import Field
from app.research import ResearchInput
from app.db import dump_json,decode_stored_json,now_iso,canonical_payload_hash


def freshness_state(reviewed_at,days,today=None):
    today=today or date.today()
    if not reviewed_at:return 'unknown'
    try: reviewed=date.fromisoformat(reviewed_at[:10])
    except (ValueError,TypeError):return 'unknown'
    if days==0:return 'policy_disabled'
    age=(today-reviewed).days
    if age<0:return 'unknown'
    return 'overdue' if age>days else 'due' if age==days else 'current'


class FreshnessReview(ResearchInput):
    expected_revision:int=Field(gt=0)
    reviewed_at:date
    reason:str=Field(min_length=1,max_length=2000)
    suspend_approach:bool=False
    question_id:int|None=Field(default=None,gt=0)


class Compensation(ResearchInput):
    expected_revision:int=Field(gt=0)
    source_event_id:int=Field(gt=0)
    side:Literal['before','after']
    fields:list[Literal['name','short_rationale']]=Field(min_length=1,max_length=2)
    reason:str=Field(min_length=1,max_length=2000)
    preview_hash:str|None=Field(default=None,min_length=64,max_length=64)


def value_diff(before,after):
    """Represent absence separately from an explicit empty or null value."""
    before=before if isinstance(before,dict) else {};after=after if isinstance(after,dict) else {}
    return [{'field':key,'before':{'present':key in before,'value':before.get(key)},'after':{'present':key in after,'value':after.get(key)}} for key in sorted(before.keys()|after.keys()) if (key in before)!=(key in after) or before.get(key)!=after.get(key)]


def install_history_routes(app,database,guard,site_detail,days=0,read_guard=None):
    auth=[Depends(guard)]
    def row_for(connection,id,expected=None):
        row=connection.execute('SELECT * FROM sites WHERE id=?',(id,)).fetchone()
        if row is None:raise HTTPException(404,'site not found')
        if expected is not None and (row['revision']!=expected or row['merged_into_id'] is not None):raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'site changed or merged'})
        return row
    @app.get('/api/sites/{site_id}/history',dependencies=[Depends(read_guard or guard)])
    def history(site_id:int,before:int|None=Query(default=None,gt=0),limit:int=Query(default=50,ge=1,le=100)):
        with database.connect() as c:
            row_for(c,site_id)
            rows=c.execute('SELECT * FROM site_events WHERE site_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',(site_id,before,before,limit+1)).fetchall()
            items=[]
            for row in rows[:limit]:
                payload,status=decode_stored_json(row['payload_json'],'object')
                items.append({'id':row['id'],'event_type':row['event_type'],'created_at':row['created_at'],'payload':payload if status in {'valid','valid_empty'} else None,'data_status':status,'diff':value_diff(payload.get('before'),payload.get('after')) if payload else [],'history_gap':row['event_type']=='import'})
            return {'items':items,'next_cursor':items[-1]['id'] if len(rows)>limit else None,'gaps':'Import events retain a receipt instead of reconstructing an unavailable full before snapshot.'}
    def effect(c,id,body):
        row=row_for(c,id,body.expected_revision)
        event=c.execute('SELECT * FROM site_events WHERE id=? AND site_id=?',(body.source_event_id,id)).fetchone()
        if not event or event['event_type']!='edit':raise HTTPException(409,'ordinary text requires a recorded edit snapshot')
        payload,status=decode_stored_json(event['payload_json'],'object')
        side=payload.get(body.side,{}) if payload else {}
        if status not in {'valid','valid_empty'} or not isinstance(side,dict) or any(key not in side for key in body.fields):raise HTTPException(409,'historical fields are unavailable')
        changes={key:side[key] for key in body.fields}
        for key,value in changes.items():
            if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>(500 if key=='name' else 2000)):raise HTTPException(409,'historical text no longer meets validation')
            if key=='name' and (value is None or 'snublestein' in value.casefold()):raise HTTPException(409,'historical name is excluded')
        receipt={'site_id':id,'revision':row['revision'],'source_event_id':event['id'],'side':body.side,'reason':body.reason,'changes':changes,'before':{key:row[key] for key in changes}}
        receipt['preview_hash']=canonical_payload_hash(receipt)
        return receipt
    @app.post('/api/sites/{site_id}/history/compensation-preview',dependencies=auth)
    def preview_compensation(site_id:int,body:Compensation):
        with database.connect() as c:return effect(c,site_id,body)
    @app.post('/api/sites/{site_id}/history/compensate',dependencies=auth)
    def compensate(site_id:int,body:Compensation):
        with database.connect() as c:
            c.execute('BEGIN IMMEDIATE');receipt=effect(c,site_id,body)
            if receipt['preview_hash']!=body.preview_hash:raise HTTPException(409,'review a fresh compensation preview')
            assignments=','.join(key+'=?' for key in receipt['changes'])
            c.execute('UPDATE sites SET '+assignments+',revision=revision+1,updated_at=? WHERE id=?',(*receipt['changes'].values(),now_iso(),site_id))
            c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'compensation',?,?)",(site_id,dump_json(receipt),now_iso()))
            return {'receipt':receipt,'site':site_detail(c,row_for(c,site_id))}
    @app.post('/api/sites/{site_id}/freshness-review',dependencies=auth)
    def review_freshness(site_id:int,body:FreshnessReview):
        if body.reviewed_at>date.today():raise HTTPException(422,'future review date cannot establish currentness')
        with database.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=row_for(c,site_id,body.expected_revision)
            if body.question_id and not c.execute('SELECT 1 FROM research_questions WHERE id=? AND external_key=?',(body.question_id,row['external_key'])).fetchone():raise HTTPException(409,'question does not belong to this site')
            c.execute('UPDATE sites SET approach_access=CASE WHEN ? THEN ? ELSE approach_access END,revision=revision+1,updated_at=? WHERE id=?',(body.suspend_approach,'unknown',now_iso(),site_id))
            c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'freshness_review',?,?)",(site_id,dump_json(body.model_dump(mode='json')),now_iso()))
            return {'review':body.model_dump(mode='json'),'site':site_detail(c,row_for(c,site_id))}
    @app.get('/api/sites/{site_id}/freshness',dependencies=auth)
    def get_freshness(site_id:int):
        with database.connect() as c:
            row=row_for(c,site_id);latest=c.execute("SELECT payload_json FROM site_events WHERE site_id=? AND event_type='freshness_review' ORDER BY id DESC LIMIT 1",(site_id,)).fetchone()
            payload,status=decode_stored_json(latest[0],'object') if latest else ({},'absent_legacy')
            return {'policy_days':days,'review':payload,'review_status':status,'source_state':freshness_state(payload.get('reviewed_at') if payload else None,days),'access_state':freshness_state(row['approach_reviewed_at'],days),'approach_suspended':bool(payload and payload.get('suspend_approach'))}
