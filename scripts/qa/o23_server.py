"""Real Bunkerkartet app on loopback with the authored benchmark seed (O23 fixture server).

Same app, routes and SQLite persistence as production; only the paid OpenRouteService call is
replaced (as tests/browser_server.py does), so nothing leaves the host.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import app.main as main_module
from app.config import Settings
from app.main import create_app
from app.routes import RouteResult
from scripts.benchmark_catalogue import _seed
from scripts.qa.o23_matrix import freeze_clock

TOKEN = "o23-audit-only"


def _stub_route(api_key, coordinates):
    return RouteResult(distance_m=1200, duration_s=900, coordinates=[coordinates[0], coordinates[-1]],
                       waypoint_indices=[0, len(coordinates) - 1])


main_module.fetch_openrouteservice = _stub_route


def _pin_clock():
    import sys
    pinned = freeze_clock(m for name, m in list(sys.modules.items()) if m is not None and name.split(".")[0] in {"app", "scripts"})
    if not callable(getattr(main_module, "now_iso", None)) or main_module.now_iso() != "2026-01-01T12:00:00+00:00" or pinned < 2:
        raise SystemExit("fixture clock could not be pinned")


_pin_clock()
app = create_app(Settings(data_dir=Path(os.environ["O23_DATA_DIR"]), admin_token=TOKEN,
                          ors_api_key="synthetic-ors-key", app_version="o23-fixture"))
_lifespan = app.router.lifespan_context


def _seed_catalogue():
    count = int(os.environ["O23_SITES"])
    if count <= 0:
        return
    with app.state.database.connect() as connection:
        if connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0]:
            return  # restart over persisted data must not reseed
    _seed(app.state.database, count)
    with app.state.database.connect() as connection:  # reviewed approach points let routes be planned
        connection.execute(
            """UPDATE sites SET approach_latitude=latitude+0.001, approach_longitude=longitude+0.001,
               approach_access='public', approach_note='Synthetic public approach',
               approach_reviewed_at='2026-01-01' WHERE id<=3""")


@asynccontextmanager
async def _o23_lifespan(instance):
    async with _lifespan(instance):
        _seed_catalogue()
        yield


app.router.lifespan_context = _o23_lifespan

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["O23_PORT"]), log_level="warning")
