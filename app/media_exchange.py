"""Versioned bounded full workspace package including qualified owned image bytes."""
from contextlib import closing
import hashlib,json,os,re,shutil,sqlite3,stat,tempfile,zipfile
from pathlib import Path
from app.interchange import create_workspace_archive,stage_workspace_archive,ExchangeError,DEFAULT_MAX_UNPACKED_BYTES,DEFAULT_MAX_ARCHIVE_BYTES
from app.image_attachments import stage_backup,restore_staged_backup,StagedAttachmentBackup,StagedFile,AttachmentError
from app.json_input import decode_json_strict

_MEDIA=re.compile(r'^images/([0-9a-f]{32})/(original\.bin|derived\.(png|jpg)|thumbnail\.jpg|receipt\.json)$')


def refs(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as c:
        return tuple('bunkerkartet-image:'+row[0] for row in c.execute("SELECT image_id FROM image_references WHERE state='active' ORDER BY image_id"))


def export_media_workspace(database,seed,attachments,archive,policy,*,project_root):
    archive=Path(archive)
    if archive.exists():raise ExchangeError('media workspace destination exists')
    archive.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bk-media-export-',dir=archive.parent) as scratch:
        root=Path(scratch);snapshot=stage_backup(attachments,policy)
        package=root/'workspace.bkws'
        create_workspace_archive(database,seed,package,project_root=project_root,_media_bundle=True)
        stage_workspace_archive(package,root/'check',_media_bundle=True)
        if refs(root/'check/data/bunkerkartet.sqlite3')!=snapshot.immutable_references:raise ExchangeError('image references and bytes differ; no package produced')
        entries={'workspace.bkws':package.read_bytes()}
        entries.update({'images/'+item.path:item.data for item in snapshot.files})
        if sum(map(len,entries.values()))>DEFAULT_MAX_UNPACKED_BYTES:raise ExchangeError('media workspace exceeds bound')
        manifest={'format':'bunkerkartet-media-workspace','version':1,'policy_id':snapshot.owner_policy_id,'references':list(snapshot.immutable_references),'snapshot_hash':snapshot.manifest_sha256,'members':{name:{'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data)} for name,data in entries.items()}}
        manifest_bytes=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        if len(manifest_bytes)>1024*1024:raise ExchangeError('media manifest exceeds bound')
        candidate=root/'full.bkmw'
        with zipfile.ZipFile(candidate,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',manifest_bytes)
            for name,data in entries.items():z.writestr(name,data)
        if candidate.stat().st_size>DEFAULT_MAX_ARCHIVE_BYTES:raise ExchangeError('media workspace archive exceeds bound')
        os.chmod(candidate,0o600);os.link(candidate,archive)
    return {'format':manifest['format'],'version':1,'member_count':len(entries)+1,'references':manifest['references'],'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}


def stage_media_workspace(archive,destination,policy):
    archive=Path(archive);destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise ExchangeError('media restore destination exists')
    if archive.stat().st_size>DEFAULT_MAX_ARCHIVE_BYTES:raise ExchangeError('media archive exceeds bound')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bk-media-stage-',dir=destination.parent) as scratch:
        root=Path(scratch);entries={}
        with zipfile.ZipFile(archive) as z:
            infos=z.infolist()
            if len(infos)>policy.max_attachments*4+2:raise ExchangeError('media member count exceeds policy')
            total=0
            for info in infos:
                mode=info.external_attr>>16
                if info.filename not in {'manifest.json','workspace.bkws'} and not _MEDIA.fullmatch(info.filename):raise ExchangeError('media member path is unsupported')
                if info.filename in entries or info.is_dir() or stat.S_IFMT(mode) not in {0,stat.S_IFREG} or info.flag_bits&1 or getattr(info,'orig_filename',info.filename)!=info.filename:raise ExchangeError('media member type is unsupported')
                total+=info.file_size
                if total>DEFAULT_MAX_UNPACKED_BYTES or (info.filename=='manifest.json' and info.file_size>1024*1024):raise ExchangeError('media expanded bytes exceed bound')
                # Decompress no more than the declared bound; ZIP CRC is checked too.
                with z.open(info) as stream:
                    data=stream.read(info.file_size+1)
                    if len(data)!=info.file_size:raise ExchangeError('media member length differs')
                entries[info.filename]=data
        manifest=decode_json_strict(entries.pop('manifest.json'),max_bytes=1024*1024)
        if not isinstance(manifest,dict) or manifest.get('format')!='bunkerkartet-media-workspace' or manifest.get('version')!=1 or manifest.get('policy_id')!=policy.owner_policy_id or set(manifest.get('members',{}))!=set(entries):raise ExchangeError('media manifest is incompatible')
        for name,data in entries.items():
            if manifest['members'][name]!={'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data)}:raise ExchangeError('media checksum mismatch')
        package=root/'workspace.bkws';package.write_bytes(entries.pop('workspace.bkws'))
        stage=root/'workspace';stage_workspace_archive(package,stage,_media_bundle=True)
        files=tuple(StagedFile(name.removeprefix('images/'),hashlib.sha256(data).hexdigest(),data) for name,data in sorted(entries.items()))
        references=tuple(manifest['references'])
        snapshot=StagedAttachmentBackup(1,policy.owner_policy_id,files,references,manifest['snapshot_hash'])
        restore_staged_backup(snapshot,stage/'data/attachments',policy)
        if refs(stage/'data/bunkerkartet.sqlite3')!=references:raise ExchangeError('restored references and bytes differ')
        if destination.exists():raise ExchangeError('media destination appeared during staging')
        os.rename(stage,destination)
    return {'status':'staged-and-verified','destination':str(destination),'automatic_live_overwrite':False,'references':list(references)}
