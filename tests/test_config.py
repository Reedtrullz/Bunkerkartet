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



def test_optional_lock_intervals_and_map_provider_policy_are_disabled_by_default():
    import pytest
    defaults=Settings()
    assert defaults.idle_lock_seconds==0 and defaults.hidden_lock_seconds==0 and defaults.map_providers==()
    for bad in (-1, True, 86401):
        with pytest.raises(ValueError): Settings(idle_lock_seconds=bad)
    with pytest.raises(ValueError): Settings(map_providers=('unknown',))


def test_public_browser_configuration_does_not_expose_credentials(tmp_path):
    from started_client import StartedClient
    from app.main import create_app
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='token-canary',ors_api_key='provider-canary')))
    response=api.get('/api/config')
    assert response.status_code==200
    assert response.json()['enabled_map_providers']==[]
    assert 'canary' not in response.text
