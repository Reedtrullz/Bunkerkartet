import hashlib
import sqlite3

import pytest

import app.db as module
from app.db import CURRENT_SCHEMA_VERSION, Database, OwnedConnection, SCHEMA, required_schema_errors
from scripts.verify_database import verify


def starting_workspace(path, version):
    with sqlite3.connect(path, factory=OwnedConnection) as connection:
        connection.row_factory=sqlite3.Row
        connection.executescript(SCHEMA)
        connection.execute('PRAGMA user_version=1')
        connection.execute("INSERT INTO sites(id,external_key,name,site_kind,precision,location_basis,created_at,updated_at) VALUES(41,'synthetic:41','Synthetic','bunker','unknown','unknown','2026-01-01','2026-01-01')")
        connection.execute("INSERT INTO site_events(id,site_id,event_type,payload_json,created_at) VALUES(71,41,'edit','{}','2026-01-01')")
        connection.execute("INSERT INTO import_batches(batch_id,schema_version,generated_at,created_at) VALUES('fixture','1.0','2026-01-01T00:00:00Z','2026-01-01')")
        connection.execute("INSERT INTO import_records(id,batch_id,external_key,site_id,action,payload_json,created_at) VALUES(91,'fixture','synthetic:41',41,'created','{\"external_key\":\"synthetic:41\",\"sources\":[]}','2026-01-01')")
        connection.commit()
        for boundary in range(2,version+1):
            module._run_migration(connection,getattr(module,f'_migrate_v{boundary}'))
        if version==0: connection.execute('PRAGMA user_version=0')


def identities(path):
    with sqlite3.connect(path,factory=OwnedConnection) as connection:
        records=connection.execute('SELECT id,payload_json FROM import_records ORDER BY id').fetchall()
        return connection.execute('SELECT id,external_key FROM sites ORDER BY id').fetchall(),connection.execute('SELECT id,site_id,event_type,payload_json FROM site_events ORDER BY id').fetchall(),[(id,hashlib.sha256(payload.encode()).hexdigest()) for id,payload in records]


@pytest.mark.parametrize('version',range(CURRENT_SCHEMA_VERSION))
def test_each_committed_boundary_rolls_back_and_resumes(tmp_path,monkeypatch,version):
    path=tmp_path/'workspace.sqlite3'
    starting_workspace(path,version)
    before=identities(path)
    next_boundary=version+1
    original=getattr(module,f'_migrate_v{next_boundary}')
    def interrupted(connection):
        original(connection)
        raise RuntimeError('synthetic interruption before commit')
    monkeypatch.setattr(module,f'_migrate_v{next_boundary}',interrupted)
    with pytest.raises(RuntimeError,match='synthetic interruption'):
        Database(path).initialize()
    with sqlite3.connect(path,factory=OwnedConnection) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0]==version
    assert identities(path)==before
    monkeypatch.setattr(module,f'_migrate_v{next_boundary}',original)
    Database(path).initialize()
    assert identities(path)==before
    verify(path,CURRENT_SCHEMA_VERSION)
    with Database(path).connect() as connection:
        assert required_schema_errors(connection)==[]
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0]==1


def test_future_version_fails_without_file_changes(tmp_path):
    path=tmp_path/'future.sqlite3'
    starting_workspace(path,CURRENT_SCHEMA_VERSION)
    with sqlite3.connect(path,factory=OwnedConnection) as connection:
        connection.execute(f'PRAGMA user_version={CURRENT_SCHEMA_VERSION+1}')
    before=path.read_bytes()
    with pytest.raises(RuntimeError,match='future schema'):
        Database(path).initialize()
    assert path.read_bytes()==before
