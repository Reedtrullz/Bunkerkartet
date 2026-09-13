import os
from pathlib import Path
from unittest.mock import patch

from app.config import Settings


def test_settings_reads_environment_without_exposing_defaults():
    with patch.dict(
        os.environ,
        {
            "BUNKERKARTET_DATA_DIR": "/tmp/bunkerkartet-test-data",
            "ADMIN_TOKEN": "test-admin-token",
            "ORS_API_KEY": "test-ors-key",
            "APP_VERSION": "test-version",
        },
        clear=False,
    ):
        settings = Settings.from_env()

    assert settings.data_dir == Path("/tmp/bunkerkartet-test-data")
    assert settings.admin_token == "test-admin-token"
    assert settings.ors_api_key == "test-ors-key"
    assert settings.app_version == "test-version"


def test_settings_defaults_to_a_local_data_directory():
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings.from_env()

    assert settings.data_dir == Path("data")
    assert settings.admin_token == ""
    assert settings.ors_api_key == ""

