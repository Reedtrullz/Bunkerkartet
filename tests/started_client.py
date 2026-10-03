"""Test client with explicit application startup and per-test shutdown ownership."""
from fastapi.testclient import TestClient

_started = []

class StartedClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lifetime_started = False
        self.__enter__()
        _started.append(self)

    def __enter__(self):
        if not self._lifetime_started:
            super().__enter__()
            self._lifetime_started = True
        return self

    def __exit__(self, *args):
        if self._lifetime_started:
            self._lifetime_started = False
            return super().__exit__(*args)

def close_started_clients():
    while _started:
        _started.pop().__exit__(None, None, None)
