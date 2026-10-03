"""Owner-gated private image references and calibration receipts; disabled by default."""
import base64,binascii,json,shutil
from pathlib import Path
from fastapi import Depends,HTTPException,Response
from pydantic import Field
from app.research import ResearchInput
from app.db import dump_json,decode_stored_json,now_iso
from app.json_input import decode_json_strict
from app.image_attachments import AttachmentPolicy,AttachmentError,store_image,read_image,delete_preview
from app.raster_evidence import CalibrationPolicy,RasterEvidenceError,build_calibration_receipt

MEDIA_SCHEMA=["""CREATE TABLE image_references (
 id INTEGER PRIMARY KEY, image_id TEXT NOT NULL UNIQUE, site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
 receipt_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','deleted')),
 created_at TEXT NOT NULL, deleted_at TEXT, deletion_reason TEXT)"""]
MEDIA_COLUMNS={'image_references':{'id','image_id','site_id','receipt_json','state','created_at','deleted_at','deletion_reason'}}


def selected_policy(path,kind):
    if path is None:return kind()
    try:return kind(**decode_json_strict(Path(path).read_bytes(),max_bytes=32000))
    except (ValueError,TypeError,OSError) as error:raise RuntimeError('owner media policy is invalid') from error


class ImageUpload(ResearchInput):
    expected_revision:int=Field(gt=0)
    mime:str=Field(max_length=30)
    bytes_base64:str=Field(min_length=1,max_length=1800000)
    reason:str=Field(min_length=1,max_length=2000)
    observation_id:int|None=Field(default=None,gt=0)
    claim_id:str|None=Field(default=None,min_length=1,max_length=100)


class Deletion(ResearchInput):
    preview_hash:str=Field(min_length=64,max_length=64)
    reason:str=Field(min_length=1,max_length=2000)


class CalibrationInput(ResearchInput):
    raster:dict=Field(max_length=12)
    control_points:list[dict]=Field(min_length=3,max_length=100)
    rights_status:str=Field(default='unknown',max_length=30)
    rights_reference:str|None=Field(default=None,max_length=200)


def install_media_routes(app,database,guard,settings,effective_content=None):
    auth=[Depends(guard)];directory=settings.data_dir/'attachments'
    image_policy=selected_policy(settings.attachment_policy_path,AttachmentPolicy)
    raster_policy=selected_policy(settings.raster_policy_path,CalibrationPolicy)
    def require_enabled():
        if not image_policy.enabled:raise HTTPException(409,'image attachments are disabled pending owner rights and retention policy')
    def reference(c,id):
        row=c.execute('SELECT * FROM image_references WHERE image_id=?',(id,)).fetchone()
        if row is None:raise HTTPException(404,'image reference not found')
        if row['state']!='active':raise HTTPException(410,'image bytes were deleted; provenance tombstone retained')
        return row
    @app.post('/api/sites/{site_id}/images',dependencies=auth)
    def upload(site_id:int,body:ImageUpload):
        require_enabled();receipt=None
        try:
            data=base64.b64decode(body.bytes_base64,validate=True)
        except (ValueError,binascii.Error):raise HTTPException(422,'image encoding is invalid')
        try:
            with database.connect() as c:
                c.execute('BEGIN IMMEDIATE');site=c.execute('SELECT * FROM sites WHERE id=?',(site_id,)).fetchone()
                if site is None:raise HTTPException(404,'site not found')
                if site['revision']!=body.expected_revision or site['merged_into_id'] is not None:raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'site changed'})
                if body.observation_id is not None and not c.execute('SELECT 1 FROM field_observations WHERE id=? AND site_id=?',(body.observation_id,site_id)).fetchone():raise HTTPException(409,'observation reference does not belong to the selected site')
                if body.claim_id is not None:
                    content,status=effective_content(site) if effective_content else (None,'unavailable')
                    if content is None or body.claim_id not in {claim.id for claim in content.claims}:raise HTTPException(409,'claim reference is unavailable in selected effective content')
                receipt=store_image(directory,data,image_policy,declared_mime=body.mime)
                c.execute('INSERT INTO image_references(image_id,site_id,receipt_json,created_at) VALUES(?,?,?,?)',(receipt['attachment_id'],site_id,dump_json(receipt),now_iso()))
                c.execute('UPDATE sites SET revision=revision+1,updated_at=? WHERE id=?',(now_iso(),site_id))
                c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'image_attached',?,?)",(site_id,dump_json({'receipt':receipt,'reason':body.reason,'observation_id':body.observation_id,'claim_id':body.claim_id}),now_iso()))
            return {'receipt':receipt,'site_revision':body.expected_revision+1}
        except AttachmentError as error:raise HTTPException(422,'image bytes or owner policy failed qualification') from error
        except BaseException:
            # Only compensate the brand-new, server-owned directory from this call.
            if receipt is not None:shutil.rmtree(directory/receipt['attachment_id'],ignore_errors=True)
            raise
    @app.get('/api/sites/{site_id}/images',dependencies=auth)
    def images(site_id:int):
        with database.connect() as c:
            return {'enabled':image_policy.enabled,'items':[{'image_id':row['image_id'],'state':row['state'],'receipt':decode_stored_json(row['receipt_json'],'object')[0]} for row in c.execute('SELECT * FROM image_references WHERE site_id=? ORDER BY id',(site_id,))]}
    @app.get('/api/images/{image_id}',dependencies=auth)
    def image(image_id:str,variant:str='derived'):
        require_enabled()
        with database.connect() as c:row=reference(c,image_id);receipt=decode_stored_json(row['receipt_json'],'object')[0]
        try:data=read_image(directory,image_id,image_policy,variant=variant)
        except AttachmentError as error:raise HTTPException(409,'image bytes failed qualification') from error
        # Derived format is identified from its encoded signature; original requires explicit query.
        mime='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        return Response(data,media_type=mime,headers={'Cache-Control':'no-store','Content-Disposition':'inline','X-Content-Type-Options':'nosniff'})
    @app.post('/api/images/{image_id}/deletion-preview',dependencies=auth)
    def preview_delete(image_id:str):
        require_enabled()
        with database.connect() as c:reference(c,image_id)
        try:return delete_preview(directory,image_id,image_policy)
        except AttachmentError as error:raise HTTPException(409,'image deletion preview unavailable') from error
    # Actual deletion remains a separately invoked owner operation. No timed retention job exists.
    @app.post('/api/raster-calibrations',dependencies=auth)
    def calibrate(body:CalibrationInput):
        try:return build_calibration_receipt(body.raster,body.control_points,rights_status=body.rights_status,rights_reference=body.rights_reference,policy=raster_policy)
        except RasterEvidenceError as error:raise HTTPException(422,'calibration inputs or owner policy require review') from error
