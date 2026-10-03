import hashlib
from pathlib import Path
import pytest
from app.db import Database,CURRENT_SCHEMA_VERSION
from scripts.verify_candidate_volume import verify_candidate


def test_candidate_requires_exact_qualified_populated_bytes_and_no_active_journal(tmp_path):
    path=tmp_path/'candidate.sqlite3';Database(path).initialize()
    with Database(path).connect() as c:
        c.execute("INSERT INTO sites(external_key,name,site_kind,precision,location_basis,created_at,updated_at) VALUES('candidate:canary','Preserved candidate','bunker','unknown','llm_inference','2026-10-03','2026-10-03')")
    original=path.read_bytes();values={'expected_sha256':hashlib.sha256(original).hexdigest(),'expected_size':len(original),'expected_version':CURRENT_SCHEMA_VERSION}
    verify_candidate(path,**values)
    assert path.read_bytes()==original
    empty=tmp_path/'empty.sqlite3';Database(empty).initialize()
    with pytest.raises(RuntimeError):verify_candidate(empty,**values)
    with pytest.raises(RuntimeError):verify_candidate(tmp_path/'missing.sqlite3',**values)
    with pytest.raises(RuntimeError):verify_candidate(path,**{**values,'expected_sha256':'0'*64})
    Path(str(path)+'-wal').write_bytes(b'active journal canary')
    with pytest.raises(RuntimeError,match='checkpointed'):verify_candidate(path,**values)
    assert path.read_bytes()==original
