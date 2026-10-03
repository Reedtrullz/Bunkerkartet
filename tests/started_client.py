"""Test client with explicit application startup and per-test shutdown ownership."""
from fastapi.testclient import TestClient

_started = []

class StartedClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__enter__()
        _started.append(self)

def close_started_clients():
    while _started:
        _started.pop().__exit__(None, None, None)
