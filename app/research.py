"""Private, revisioned research questions and explicitly unresolved alternatives."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from app.db import decode_stored_json, dump_json, now_iso
from app.imports import validate_reference_url
from urllib.parse import urlsplit


RESEARCH_SCHEMA = [
    """CREATE TABLE research_questions (
        id INTEGER PRIMARY KEY, external_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('identity','location','access','source','other')),
        state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','deferred','resolved')),
        payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX idx_research_questions_key ON research_questions(external_key,state,id)",
    """CREATE TABLE question_events (
        id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES research_questions(id) ON DELETE CASCADE,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE identity_hypotheses (
        id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES research_questions(id) ON DELETE CASCADE,
        state TEXT NOT NULL DEFAULT 'retained' CHECK(state IN ('retained','rejected','deferred')),
        payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE claim_assertions (
        id INTEGER PRIMARY KEY, external_key TEXT NOT NULL, target_external_key TEXT NOT NULL,
        claim_id TEXT NOT NULL, target_claim_id TEXT NOT NULL,
        relation TEXT NOT NULL CHECK(relation IN ('supports','contradicts','derived_from')),
        state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved','reopened')),
        payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
]
RESEARCH_COLUMNS = {
    'research_questions': {'id','external_key','kind','state','payload_json','revision','created_at','updated_at'},
    'question_events': {'id','question_id','payload_json','created_at'},
    'identity_hypotheses': {'id','question_id','state','payload_json','revision','created_at','updated_at'},
    'claim_assertions': {'id','external_key','target_external_key','claim_id','target_claim_id','relation','state','payload_json','revision','created_at','updated_at'},
}


class ResearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    @field_validator('*')
    @classmethod
    def require_nonblank_text(cls, value):
        if isinstance(value,str) and not value.strip(): raise ValueError('text cannot be blank')
        return value


class QuestionInput(ResearchInput):
    external_key: str = Field(min_length=1,max_length=300)
    kind: Literal['identity','location','access','source','other']
    wording: str = Field(min_length=1,max_length=2000)
    uncertainty: str | None = Field(default=None,max_length=2000)
    proposed_action: str | None = Field(default=None,max_length=1000)
    source_urls: list[str] = Field(default_factory=list,max_length=20)
    evidence_ids: list[int] = Field(default_factory=list,max_length=30)
    claim_ids: list[str] = Field(default_factory=list,max_length=30)

    @field_validator('source_urls')
    @classmethod
    def safe_urls(cls, value):
        for url in value:
            validate_reference_url(url)
            if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
                raise ValueError("source URL must be HTTP(S)")
        return value

    @field_validator('claim_ids')
    @classmethod
    def bounded_ids(cls, value):
        if len(set(value))!=len(value) or any(not item.strip() or len(item)>100 for item in value):raise ValueError('claim IDs must be bounded and unique')
        return value

    @field_validator('evidence_ids')
    @classmethod
    def positive_ids(cls,value):
        if len(set(value))!=len(value) or any(item<=0 for item in value):raise ValueError('evidence IDs must be positive and unique')
        return value


class QuestionDecision(ResearchInput):
    expected_revision: int = Field(gt=0)
    state: Literal['open','deferred','resolved']
    reason: str = Field(min_length=1,max_length=2000)


class HypothesisPoint(ResearchInput):
    lat: float = Field(ge=-90,le=90)
    lon: float = Field(ge=-180,le=180)


class HypothesisInput(ResearchInput):
    expected_question_revision: int = Field(gt=0)
    label: str = Field(min_length=1,max_length=300)
    original_wording: str = Field(min_length=1,max_length=2000)
    objections: str | None = Field(default=None,max_length=2000)
    candidate_point: HypothesisPoint | None = None
    source_urls: list[str] = Field(default_factory=list,max_length=20)
    claim_ids: list[str] = Field(default_factory=list,max_length=30)

    @field_validator('source_urls')
    @classmethod
    def safe_urls(cls,value):return QuestionInput.safe_urls(value)


class HypothesisDecision(ResearchInput):
    expected_revision: int = Field(gt=0)
    expected_question_revision: int = Field(gt=0)
    state: Literal['retained','rejected','deferred']
    reason: str = Field(min_length=1,max_length=2000)


class AssertionInput(ResearchInput):
    external_key: str = Field(min_length=1,max_length=300)
    target_external_key: str = Field(min_length=1,max_length=300)
    claim_id: str = Field(min_length=1,max_length=100)
    target_claim_id: str = Field(min_length=1,max_length=100)
    relation: Literal['supports','contradicts','derived_from']
    reason: str = Field(min_length=1,max_length=2000)


class AssertionDecision(ResearchInput):
    expected_revision: int = Field(gt=0)
    state: Literal['resolved','reopened']
    reason: str = Field(min_length=1,max_length=2000)


def stored_record(row):
    result=dict(row)
    payload,status=decode_stored_json(result.pop('payload_json'),'object')
    if status not in {'valid','valid_empty'}:
        return {**result,'data_status':'unavailable','stored_field_status':status}
    return {**payload,**result,'data_status':'valid'}


def require_revision(row, expected):
    if row['revision']!=expected:
        raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'research record changed; reload before deciding'})


def install_research_routes(app,database,admin_guard,effective_content):
    auth=[Depends(admin_guard)]

    def question_row(connection,id):
        row=connection.execute('SELECT * FROM research_questions WHERE id=?',(id,)).fetchone()
        if row is None:raise HTTPException(404,'question not found')
        if stored_record(row)['data_status']!='valid':raise HTTPException(409,'question stored data unavailable')
        return row

    def event(connection,id,before,after):
        connection.execute('INSERT INTO question_events(question_id,payload_json,created_at) VALUES(?,?,?)',(id,dump_json({'before':before,'after':after}),now_iso()))

    @app.post('/api/research/questions',dependencies=auth,status_code=201)
    def create_question(body:QuestionInput):
        with database.connect() as connection:
            timestamp=now_iso();payload=body.model_dump(mode='json',exclude={'external_key','kind'})
            if body.claim_ids:
                row=connection.execute('SELECT * FROM sites WHERE external_key=?',(body.external_key,)).fetchone()
                content,status=effective_content(row) if row else (None,'absent')
                if content is None or not set(body.claim_ids)<={claim.id for claim in content.claims}:raise HTTPException(422,'question references unavailable claim')
            if body.evidence_ids:
                placeholders=','.join('?' for _ in body.evidence_ids)
                count=connection.execute(f'SELECT COUNT(*) FROM evidence_items JOIN sites ON sites.id=evidence_items.site_id WHERE sites.external_key=? AND evidence_items.id IN ({placeholders})',(body.external_key,*body.evidence_ids)).fetchone()[0]
                if count!=len(body.evidence_ids):raise HTTPException(422,'evidence does not belong to this site')
            cursor=connection.execute('INSERT INTO research_questions(external_key,kind,payload_json,created_at,updated_at) VALUES(?,?,?,?,?)',(body.external_key,body.kind,dump_json(payload),timestamp,timestamp))
            result=stored_record(question_row(connection,cursor.lastrowid));event(connection,result['id'],None,result)
            return result

    @app.get('/api/research/questions',dependencies=auth)
    def list_questions(external_key:str|None=Query(default=None,max_length=300),state:Literal['open','deferred','resolved']|None=None,before:int|None=Query(default=None,gt=0),limit:int=Query(default=50,ge=1,le=100)):
        with database.connect() as connection:
            rows=connection.execute('SELECT * FROM research_questions WHERE (? IS NULL OR external_key=?) AND (? IS NULL OR state=?) AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',(external_key,external_key,state,state,before,before,limit+1)).fetchall()
            return {'items':[stored_record(row) for row in rows[:limit]],'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}

    @app.get('/api/research/questions/{id}',dependencies=auth)
    def get_question(id:int):
        with database.connect() as connection:
            result=stored_record(question_row(connection,id))
            result['hypotheses']=[stored_record(row) for row in connection.execute('SELECT * FROM identity_hypotheses WHERE question_id=? ORDER BY id',(id,))]
            return result

    @app.patch('/api/research/questions/{id}',dependencies=auth)
    def decide_question(id:int,body:QuestionDecision):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');row=question_row(connection,id);require_revision(row,body.expected_revision)
            before=stored_record(row);payload=decode_stored_json(row['payload_json'],'object')[0]
            payload['decision_reason']=body.reason
            connection.execute('UPDATE research_questions SET state=?,payload_json=?,revision=revision+1,updated_at=? WHERE id=?',(body.state,dump_json(payload),now_iso(),id))
            after=stored_record(question_row(connection,id));event(connection,id,before,after);return after

    @app.get('/api/research/questions/{id}/events',dependencies=auth)
    def question_history(id:int,before:int|None=Query(default=None,gt=0),limit:int=Query(default=50,ge=1,le=100)):
        with database.connect() as connection:
            question_row(connection,id)
            return [stored_record(row) for row in connection.execute('SELECT * FROM question_events WHERE question_id=? AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',(id,before,before,limit))]

    @app.post('/api/research/questions/{id}/hypotheses',dependencies=auth,status_code=201)
    def add_hypothesis(id:int,body:HypothesisInput):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');question=question_row(connection,id);require_revision(question,body.expected_question_revision)
            if question['kind'] not in {'identity','location'}:raise HTTPException(422,'hypotheses require an identity or location question')
            timestamp=now_iso();cursor=connection.execute('INSERT INTO identity_hypotheses(question_id,payload_json,created_at,updated_at) VALUES(?,?,?,?)',(id,dump_json(body.model_dump(mode='json',exclude={'expected_question_revision'})),timestamp,timestamp))
            connection.execute('UPDATE research_questions SET revision=revision+1,updated_at=? WHERE id=?',(timestamp,id))
            result=stored_record(connection.execute('SELECT * FROM identity_hypotheses WHERE id=?',(cursor.lastrowid,)).fetchone())
            event(connection,id,None,{'hypothesis':result});return {**result,'question_revision':question['revision']+1}

    @app.patch('/api/research/hypotheses/{id}',dependencies=auth)
    def decide_hypothesis(id:int,body:HypothesisDecision):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');row=connection.execute('SELECT * FROM identity_hypotheses WHERE id=?',(id,)).fetchone()
            if row is None:raise HTTPException(404,'hypothesis not found')
            question=question_row(connection,row['question_id']);require_revision(question,body.expected_question_revision);require_revision(row,body.expected_revision)
            before=stored_record(row);payload,status=decode_stored_json(row['payload_json'],'object')
            if status not in {'valid','valid_empty'}:raise HTTPException(409,'hypothesis stored data unavailable')
            payload['decision_reason']=body.reason;timestamp=now_iso()
            connection.execute('UPDATE identity_hypotheses SET state=?,payload_json=?,revision=revision+1,updated_at=? WHERE id=?',(body.state,dump_json(payload),timestamp,id))
            connection.execute('UPDATE research_questions SET revision=revision+1,updated_at=? WHERE id=?',(timestamp,question['id']))
            after=stored_record(connection.execute('SELECT * FROM identity_hypotheses WHERE id=?',(id,)).fetchone());event(connection,question['id'],{'hypothesis':before},{'hypothesis':after})
            return {**after,'question_revision':question['revision']+1}

    @app.post('/api/research/assertions',dependencies=auth,status_code=201)
    def add_assertion(body:AssertionInput):
        with database.connect() as connection:
            for key,claim in ((body.external_key,body.claim_id),(body.target_external_key,body.target_claim_id)):
                row=connection.execute('SELECT * FROM sites WHERE external_key=?',(key,)).fetchone()
                content,status=effective_content(row) if row else (None,'absent')
                if content is None or claim not in {item.id for item in content.claims}:raise HTTPException(422,'assertion references unavailable claim')
            timestamp=now_iso();cursor=connection.execute('INSERT INTO claim_assertions(external_key,target_external_key,claim_id,target_claim_id,relation,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(body.external_key,body.target_external_key,body.claim_id,body.target_claim_id,body.relation,dump_json({'reason':body.reason,'decision_history':[]}),timestamp,timestamp))
            return stored_record(connection.execute('SELECT * FROM claim_assertions WHERE id=?',(cursor.lastrowid,)).fetchone())

    @app.get('/api/research/assertions',dependencies=auth)
    def list_assertions(external_key:str=Query(max_length=300)):
        with database.connect() as connection:
            return [stored_record(row) for row in connection.execute('SELECT * FROM claim_assertions WHERE external_key=? OR target_external_key=? ORDER BY id',(external_key,external_key))]

    @app.patch('/api/research/assertions/{id}',dependencies=auth)
    def decide_assertion(id:int,body:AssertionDecision):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');row=connection.execute('SELECT * FROM claim_assertions WHERE id=?',(id,)).fetchone()
            if row is None:raise HTTPException(404,'assertion not found')
            require_revision(row,body.expected_revision);payload,status=decode_stored_json(row['payload_json'],'object')
            if status not in {'valid','valid_empty'}:raise HTTPException(409,'assertion stored data unavailable')
            payload['decision_history'].append({'state':body.state,'reason':body.reason,'reviewed_revision':body.expected_revision,'created_at':now_iso()})
            if len(payload['decision_history'])>100:raise HTTPException(409,'assertion decision history limit reached')
            connection.execute('UPDATE claim_assertions SET state=?,payload_json=?,revision=revision+1,updated_at=? WHERE id=?',(body.state,dump_json(payload),now_iso(),id))
            return stored_record(connection.execute('SELECT * FROM claim_assertions WHERE id=?',(id,)).fetchone())
