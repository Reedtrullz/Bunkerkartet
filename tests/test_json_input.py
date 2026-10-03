import json
import pytest
from started_client import StartedClient as TestClient
from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"outer":{"x":1,"x":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'[' * 65 + b'0' + b']' * 65])
def test_strict_json_rejects_ambiguous_or_unbounded_input(raw):
    from app.json_input import decode_json_strict
    with pytest.raises(ValueError):
        decode_json_strict(raw, max_bytes=2 * 1024 * 1024, max_depth=64)


def test_strict_json_preserves_clean_semantics_and_brackets_in_strings():
    from app.json_input import decode_json_strict
    payload = {"name": "ÆØÅ [ literal ]", "items": [1, {"quoted": '\\"'}]}
    assert decode_json_strict(json.dumps(payload).encode(), max_bytes=4096, max_depth=3) == payload


@pytest.mark.parametrize("method,path", [("patch", "/api/sites/1"), ("post", "/api/sites/1/observations"), ("post", "/api/routes")])
def test_all_json_mutations_bound_streamed_bytes_before_model_parsing(tmp_path, method, path):
    client = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="test")))
    chunks = (chunk for chunk in [b'{"padding":"', b'x' * (2 * 1024 * 1024), b'"}'])
    response = getattr(client, method)(path, content=chunks, headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
    assert response.status_code == 413


def test_api_rejects_duplicate_nested_json_without_echoing_input(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="test")))
    response = client.post('/api/admin/imports/preview', content=b'{"secret-excerpt":{"x":1,"x":2}}', headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
    assert response.status_code == 422
    assert 'secret-excerpt' not in response.text


@pytest.mark.parametrize("change", [{"name": "   "}, {"warnings": ["x"] * 31}, {"warnings": ["x" * 1001]}])
def test_mutation_field_limits_fail_before_site_lookup(tmp_path, change):
    client = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="test")))
    response = client.patch('/api/sites/1', json={"expected_revision": 1, **change}, headers={"Authorization": "Bearer test"})
    assert response.status_code == 422


def test_related_key_length_is_bounded():
    from app.imports import ImportRecord
    from pydantic import ValidationError
    raw = json.loads(__import__('pathlib').Path('research/example-import.json').read_text())['records'][0]
    with pytest.raises(ValidationError):
        ImportRecord.model_validate({**raw, "related_site_keys": ['x' * 301]})
