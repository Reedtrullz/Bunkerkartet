import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest


def _docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, text=True, capture_output=True, timeout=60
    )


def test_image_runs_nonroot_with_synthetic_volume_and_reports_exact_readiness():
    image = os.environ.get("BUNKERKARTET_SMOKE_IMAGE")
    if not image or not shutil.which("docker"):
        pytest.skip("CI supplies a built image and Docker; no container smoke requested locally")

    name = "bunkerkartet-smoke-" + uuid.uuid4().hex[:10]
    volume = name + "-data"
    version = "a" * 40
    created_volume = False
    started_container = False
    try:
        _docker("volume", "create", volume)
        created_volume = True
        _docker(
            "run", "--rm", "--volume", f"{volume}:/app/data", "--entrypoint", "python", image,
            "-c", "from pathlib import Path; Path('/app/data/uid10001-write-probe').write_text('ok')",
        )
        probe = _docker("run", "--rm", "--volume", f"{volume}:/app/data", "--entrypoint", "stat", image, "-c", "%u", "/app/data/uid10001-write-probe")
        assert probe.stdout.strip() == "10001"

        # Qualify a populated online snapshot and fresh restore as the image's actual UID.
        prepare = """from pathlib import Path
from app.db import Database,CURRENT_SCHEMA_VERSION
from scripts.backup_database import create_backup
from scripts.restore_database import restore_archive
root=Path('/app/data');db=Database(root/'source.sqlite3');db.initialize()
with db.connect() as connection:
    connection.execute("INSERT INTO sites(external_key,name,site_kind,precision,location_basis,created_at,updated_at) VALUES('synthetic:container-restore','Synthetic restored catalogue','bunker','unknown','llm_inference','2026-10-03','2026-10-03')")
# Keep a WAL-mode source handle owned while the online backup reads committed data.
with db.connect() as connection:
    connection.execute("UPDATE sites SET short_rationale='Committed WAL restore canary'")
    connection.commit()
    create_backup(db.path,root/'rollback.tar.gz')
restore_archive(root/'rollback.tar.gz',root/'staged',expected_version=CURRENT_SCHEMA_VERSION)
with Database(root/'staged/bunkerkartet.sqlite3').connect() as connection:
    connection.execute("UPDATE sites SET condition='UID10001 synthetic transaction'")
    assert connection.execute('SELECT COUNT(*) FROM sites').fetchone()[0]==1
"""
        _docker('run','--rm','--volume',f'{volume}:/app/data','--entrypoint','python',image,'-c',prepare)

        _docker(
            "run", "--detach", "--name", name, "--env", "ADMIN_TOKEN=synthetic-only",
            "--env", f"APP_VERSION={version}", "--env", "BUNKERKARTET_DATA_DIR=/app/data/staged", "--publish", "127.0.0.1::8000",
            "--volume", f"{volume}:/app/data", image,
        )
        started_container = True
        mapped = _docker("port", name, "8000/tcp").stdout.strip().split(":")[-1]
        base = f"http://127.0.0.1:{mapped}"
        deadline = time.monotonic() + 35
        last_error = "container did not become ready"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/ready", timeout=2) as response:
                    readiness = json.load(response)
                with urllib.request.urlopen(base + "/api/version", timeout=2) as response:
                    actual_version = json.load(response)
                break
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                time.sleep(0.5)
        else:
            logs = _docker("logs", name, check=False).stdout
            raise AssertionError(f"container readiness timeout: {last_error}\n{logs[-2000:]}")

        assert readiness == {
            "status": "ready",
            "database": "ready",
            "authentication": "configured",
            "routing": "optional-unconfigured",
        }
        assert actual_version == {"version": version}
        assert _docker("exec", name, "id", "-u").stdout.strip() == "10001"
        request=urllib.request.Request(base+'/api/sites',headers={'Authorization':'Bearer synthetic-only'})
        with urllib.request.urlopen(request,timeout=3) as response: sites=json.load(response)
        assert len(sites)==1 and sites[0]['short_rationale']=='Committed WAL restore canary'
        assert sites[0]['condition']=='UID10001 synthetic transaction' 

        _docker("rm", "--force", name)
        started_container = False
        _docker(
            "run", "--detach", "--name", name, "--env", f"APP_VERSION={version}",
            "--publish", "127.0.0.1::8000", "--volume", f"{volume}:/app/data", image,
        )
        started_container = True
        mapped = _docker("port", name, "8000/tcp").stdout.strip().split(":")[-1]
        deadline = time.monotonic() + 15
        last_error = "container did not reject readiness without authentication"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{mapped}/api/ready", timeout=2) as response:
                    raise AssertionError(f"unauthenticated image unexpectedly became ready: HTTP {response.status}")
            except urllib.error.HTTPError as response:
                assert response.code == 503
                assert json.loads(response.read())["authentication"] == "not_configured"
                break
            except (OSError, urllib.error.URLError) as exc:
                last_error = str(exc)
                time.sleep(0.25)
        else:
            logs = _docker("logs", name, check=False).stdout
            raise AssertionError(f"missing-auth readiness timeout: {last_error}\n{logs[-2000:]}")
    finally:
        if started_container:
            _docker("rm", "--force", name, check=False)
        if created_volume:
            _docker("volume", "rm", volume, check=False)
