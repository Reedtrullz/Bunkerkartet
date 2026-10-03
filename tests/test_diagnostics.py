import json
import logging
import re

from test_import_api import client,auth,commit_previewed,package


def test_database_error_has_safe_correlation_without_private_canaries(tmp_path,monkeypatch,caplog):
    import sqlite3
    api=client(tmp_path)
    assert commit_previewed(api,package()).status_code==200
    def fail():raise sqlite3.OperationalError('private-note-canary token-canary lat=63.123456')
    monkeypatch.setattr(api.app.state.database,'connect',fail)
    with caplog.at_level(logging.WARNING,logger='bunkerkartet.operations'):
        response=api.get('/api/sites?q=private-query-canary',headers={'Authorization':'Bearer secret','X-Request-ID':'client-canary'})
    assert response.status_code==503,response.text
    correlation=response.headers['x-request-id']
    assert re.fullmatch('[a-f0-9]{32}',correlation)
    entry=[json.loads(record.message) for record in caplog.records if record.name=='bunkerkartet.operations'][-1]
    assert entry['request_id']==correlation and entry['code']=='DATABASE_UNAVAILABLE'
    for canary in ('private-note-canary','token-canary','63.123456','private-query-canary','client-canary','Bearer'):
        assert canary not in caplog.text and canary not in response.text


def test_bounded_operational_log_handler_is_optional_and_rotates(tmp_path):
    from app.diagnostics import operational_logger
    logger=operational_logger(tmp_path/'logs',max_bytes=300,backup_count=2)
    for index in range(30):logger.warning(json.dumps({'event':'synthetic','code':'DATABASE_UNAVAILABLE','request_id':'0'*32}))
    for handler in list(logger.handlers):handler.flush();handler.close();logger.removeHandler(handler)
    files=list((tmp_path/'logs').glob('operations.jsonl*'))
    assert 1<=len(files)<=3
    assert all(file.stat().st_size<500 for file in files)
