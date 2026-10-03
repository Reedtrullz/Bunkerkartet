import sqlite3

import pytest

from app.db import CURRENT_SCHEMA_VERSION, Database, required_schema_errors
from scripts.verify_database import verify


def test_missing_request_index_fails_qualification_without_writes(tmp_path):
    path = tmp_path / 'test.sqlite3'
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute('DROP INDEX idx_field_observations_request')
    before = path.read_bytes()
    with pytest.raises(RuntimeError): verify(path, CURRENT_SCHEMA_VERSION)
    assert path.read_bytes() == before


def test_same_columns_without_primary_key_and_fk_are_rejected(tmp_path):
    database = Database(tmp_path / 'test.sqlite3')
    database.initialize()
    with database.connect() as connection:
        connection.execute('CREATE TABLE bad AS SELECT * FROM site_relations')
        connection.execute('DROP TABLE site_relations')
        connection.execute('ALTER TABLE bad RENAME TO site_relations')
        assert required_schema_errors(connection)


def test_invalid_revision_and_unpaired_coordinates_are_rejected(tmp_path):
    from test_route_api import seed_site
    from test_import_api import client
    api = client(tmp_path)
    site_id = seed_site(api)
    with api.app.state.database.connect() as connection:
        connection.execute('UPDATE sites SET revision=0,longitude=NULL WHERE id=?', (site_id,))
        assert required_schema_errors(connection)
