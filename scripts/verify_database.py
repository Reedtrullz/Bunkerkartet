from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import CURRENT_SCHEMA_VERSION, REQUIRED_SCHEMA, required_schema_errors


def verify(path: Path, expected_version: int) -> None:
    if not path.is_file():
        raise RuntimeError("database file is missing")
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != expected_version or version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError("database schema version does not match expectation")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("database foreign key check failed")
        if required_schema_errors(connection):
            raise RuntimeError("database is missing required schema")
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in REQUIRED_SCHEMA
        }
    print(
        f"version={version} integrity_check=ok foreign_key_check=empty "
        + " ".join(f"{table}={count}" for table, count in counts.items())
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SQLite restore verifier")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expected-version", type=int, required=True)
    args = parser.parse_args()
    try:
        verify(args.database, args.expected_version)
    except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError):
        print("database verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
