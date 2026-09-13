import sqlite3

from app.db import Database


def test_database_initialization_creates_v1_tables(tmp_path):
    database = Database(tmp_path / "bunkerkartet.sqlite3")
    database.initialize()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "sites",
        "sources",
        "evidence",
        "import_batches",
        "import_records",
        "route_plans",
    }.issubset(names)


def test_database_enables_foreign_keys(tmp_path):
    database = Database(tmp_path / "bunkerkartet.sqlite3")
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
