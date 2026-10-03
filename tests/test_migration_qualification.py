from pathlib import Path
import hashlib,sqlite3
from app import db
from scripts.backup_database import create_backup
from scripts.qualify_migration import qualify_migration
from scripts.release_receipt import build_release_receipt,ReleaseReceiptError
import pytest


def test_schema_upgrade_preserves_real_legacy_backup_and_rollback_volume(tmp_path):
    source=tmp_path/'legacy.sqlite3'
    with sqlite3.connect(source) as c:
        c.row_factory=sqlite3.Row
        c.executescript(db.SCHEMA);c.execute('PRAGMA user_version=1')
        for version in range(2,9):db._run_migration(c,getattr(db,f'_migrate_v{version}'))
        c.execute("INSERT INTO sites(external_key,name,site_kind,precision,location_basis,created_at,updated_at) VALUES('synthetic:legacy','Legacy','bunker','unknown','map_reference','2026-10-03','2026-10-03')")
    archive=tmp_path/'rollback.tar.gz';backup=create_backup(source,archive);before=source.read_bytes()
    result=qualify_migration(archive,tmp_path/'candidate',source_version=8,candidate_data_volume='new-generation',rollback_data_volume='old-generation')
    assert source.read_bytes()==before and result['schema_after']==db.CURRENT_SCHEMA_VERSION
    kwargs=dict(commit_sha='a'*40,image_digest='sha256:'+'b'*64,database_schema_version=db.CURRENT_SCHEMA_VERSION,backup=backup,selected_port=8000,readiness={'status':'ready','database':'ready','authentication':'configured'},version={'version':'a'*40},rollback={'image_digest':'sha256:'+'c'*64,'database_schema_version':8,'data_volume':'old-generation','backup_sha256':backup['sha256']})
    with pytest.raises(ReleaseReceiptError):build_release_receipt(**kwargs)
    for missing in ('database_sha256','database_size_bytes'):
        incomplete={key:value for key,value in result.items() if key!=missing}
        with pytest.raises(ReleaseReceiptError):build_release_receipt(**kwargs,migration_qualification=incomplete)
    receipt=build_release_receipt(**kwargs,migration_qualification=result)
    assert receipt['backup']['database_schema_version']==8 and receipt['rollback']['database_schema_version']==8
