#!/usr/bin/env python3
"""Create a private, checksummed Bunkerkartet workspace exchange archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.interchange import (
    DEFAULT_MAX_ARCHIVE_BYTES,
    DEFAULT_MAX_UNPACKED_BYTES,
    ExchangeError,
    create_workspace_archive,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-unpacked-bytes", type=int, default=DEFAULT_MAX_UNPACKED_BYTES)
    args = parser.parse_args(argv)
    try:
        result = create_workspace_archive(
            args.database,
            args.seed,
            args.archive,
            project_root=args.project_root,
            max_archive_bytes=args.max_archive_bytes,
            max_unpacked_bytes=args.max_unpacked_bytes,
        )
    except (ExchangeError, OSError, RuntimeError, ValueError):
        print("workspace export failed; source data was retained", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
