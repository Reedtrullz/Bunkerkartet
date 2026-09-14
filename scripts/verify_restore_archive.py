from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path


ALLOWED_FILES = {
    "bunkerkartet.sqlite3",
    "bunkerkartet.sqlite3-wal",
    "bunkerkartet.sqlite3-shm",
}


def verify_archive(path: Path) -> int:
    seen: set[str] = set()
    root_seen = False
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name in {".", "./"}:
                if root_seen or not member.isdir():
                    raise ValueError("invalid archive root")
                root_seen = True
                continue
            name = member.name[2:] if member.name.startswith("./") else member.name
            if (
                member.name.startswith("/")
                or ".." in name.split("/")
                or name not in ALLOWED_FILES
                or not member.isreg()
                or member.issym()
                or member.islnk()
                or name in seen
            ):
                raise ValueError("invalid archive member")
            seen.add(name)
    if "bunkerkartet.sqlite3" not in seen:
        raise ValueError("database file is missing")
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Bunkerkartet SQLite backup archive")
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = verify_archive(args.archive)
    except (OSError, tarfile.TarError, ValueError):
        print("restore archive verification failed", file=sys.stderr)
        return 1
    print(f"archive=ok files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
