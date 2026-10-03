"""Read reconciliation keeps registry, dated reading and overlay identities distinct."""
from datetime import date
import hashlib

from fastapi import Depends, HTTPException, Query
from pydantic import Field, field_validator

from app.db import decode_stored_json, dump_json, now_iso
from app.research import ResearchInput, QuestionInput, require_revision


class SourceReview(ResearchInput):
    url: str = Field(max_length=8192)
    expected_revision: int = Field(ge=0)
    reviewed_at: date
    note: str = Field(min_length=1,max_length=2000)

    @field_validator('url')
    @classmethod
    def safe_url(cls,value):
        QuestionInput.safe_urls([value]);return value


def install_source_routes(app,database,admin_guard,effective_content,source_rows):
    auth=[Depends(admin_guard)]

    def source_index(connection):
        result={}
        for row in connection.execute('SELECT id,url,title,source_type FROM sources ORDER BY id'):
            try:QuestionInput.safe_urls([row['url']])
            except ValueError:continue
            item=result.setdefault(row['url'],{'url':row['url'],'registry_ids':[],'overlay_references':[],'claim_references':[],'readings':[]})
            item['registry_ids'].append(row['id'])
        for site in connection.execute('SELECT * FROM sites ORDER BY id'):
            for reading in source_rows(connection,site['id']):
                if not reading['url']:continue
                item=result.setdefault(reading['url'],{'url':reading['url'],'registry_ids':[],'overlay_references':[],'claim_references':[],'readings':[]})
                item['readings'].append({**reading,'site_id':site['id'],'external_key':site['external_key']})
            content,status=effective_content(site)
            if content is None:continue
            for source in content.sources:
                url=str(source.url);item=result.setdefault(url,{'url':url,'registry_ids':[],'overlay_references':[],'claim_references':[],'readings':[]})
                linked=[claim for claim in content.claims if source.id in claim.source_ids]
                item['overlay_references'].append({'external_key':site['external_key'],'site_id':site['id'],'source_id':source.id,'title':source.title,'unused':not linked,'content_status':status})
                item['claim_references'].extend({'external_key':site['external_key'],'site_id':site['id'],'claim_id':claim.id,'source_id':source.id,'text':claim.text,'certainty':claim.certainty} for claim in linked)
        for item in result.values():
            item['reading_count']=len({reading['evidence_id'] for reading in item['readings'] if reading['evidence_id'] is not None})
            item['unused_overlay_sources']=sum(source['unused'] for source in item['overlay_references'])
            key='source-review:'+hashlib.sha256(item['url'].encode()).hexdigest()
            review=connection.execute("SELECT * FROM research_questions WHERE external_key=? AND kind='source' ORDER BY id DESC LIMIT 1",(key,)).fetchone()
            item['review_revision']=review['revision'] if review else 0
            item['review']=decode_stored_json(review['payload_json'],'object')[0] if review else None
        return result

    @app.get('/api/research/sources',dependencies=auth)
    def list_sources(q:str|None=Query(default=None,max_length=200),unused_only:bool=False,after:str|None=Query(default=None,max_length=8192),limit:int=Query(default=50,ge=1,le=100)):
        with database.connect() as connection:
            items=[]
            for url,item in sorted(source_index(connection).items()):
                if after is not None and url<=after:continue
                if unused_only and not item['unused_overlay_sources']:continue
                if q and q.casefold() not in str(item).casefold():continue
                items.append({key:value for key,value in item.items() if key not in {'readings','claim_references'}})
            return {'items':items[:limit],'next_cursor':items[limit-1]['url'] if len(items)>limit else None}

    @app.get('/api/research/source',dependencies=auth)
    def get_source(url:str=Query(max_length=8192)):
        try:QuestionInput.safe_urls([url])
        except ValueError as error:raise HTTPException(422,'invalid source URL') from error
        with database.connect() as connection:
            item=source_index(connection).get(url)
            if item is None:raise HTTPException(404,'source not found')
            return item

    @app.post('/api/research/source-reviews',dependencies=auth)
    def review_source(body:SourceReview):
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');index=source_index(connection);item=index.get(body.url)
            if item is None:raise HTTPException(404,'source not found')
            if item['review_revision']!=body.expected_revision:raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'source review changed'})
            key='source-review:'+hashlib.sha256(body.url.encode()).hexdigest();timestamp=now_iso()
            payload={'wording':body.note,'reviewed_at':body.reviewed_at.isoformat(),'source_urls':[body.url],'decision_reason':body.note}
            old=connection.execute("SELECT * FROM research_questions WHERE external_key=? AND kind='source' ORDER BY id DESC LIMIT 1",(key,)).fetchone()
            if old:
                connection.execute('UPDATE research_questions SET payload_json=?,revision=revision+1,updated_at=? WHERE id=?',(dump_json(payload),timestamp,old['id']));id=old['id']
            else:
                id=connection.execute("INSERT INTO research_questions(external_key,kind,state,payload_json,created_at,updated_at) VALUES(?,'source','resolved',?,?,?)",(key,dump_json(payload),timestamp,timestamp)).lastrowid
            connection.execute('INSERT INTO question_events(question_id,payload_json,created_at) VALUES(?,?,?)',(id,dump_json({'before':item['review'],'after':payload}),timestamp))
            linked={reading['external_key'] for reading in item['readings']}|{ref['external_key'] for ref in item['overlay_references']}
            return {'url':body.url,'review_revision':body.expected_revision+1,'review':payload,'follow_up_suggestions':[{'external_key':key,'suggested_question':'Review source-dependent claims; no site status or access was changed'} for key in sorted(linked)]}
