import pytest
from started_client import close_started_clients

@pytest.fixture(autouse=True)
def own_started_test_clients():
    yield
    close_started_clients()
