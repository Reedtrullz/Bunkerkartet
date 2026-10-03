import subprocess
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_module_import_and_construction_are_inert(tmp_path):
    directory=tmp_path/'protected'/'never-created'
    env={**os.environ,'BUNKERKARTET_DATA_DIR':str(directory)}
    subprocess.run([sys.executable,'-c','import app.main; app.main.create_app()'],env=env,check=True)
    assert not directory.exists()


def test_lifespan_initializes_only_selected_instance(tmp_path):
    a=create_app(Settings(data_dir=tmp_path/'a',admin_token='secret'))
    b=create_app(Settings(data_dir=tmp_path/'b',admin_token='secret'))
    assert not a.state.database.path.exists() and not b.state.database.path.exists()
    with TestClient(a) as api:
        assert api.get('/api/ready').status_code==200
        assert a.state.database.path.exists() and not b.state.database.path.exists()


def test_startup_failure_is_fail_closed(tmp_path):
    directory=tmp_path/'blocked'
    directory.write_text('synthetic blocker')
    app=create_app(Settings(data_dir=directory))
    with pytest.raises((OSError,RuntimeError)):
        with TestClient(app): pass
