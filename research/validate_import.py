#!/usr/bin/env python3
"""Validate a Bunkerkartet import package without contacting the app."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.imports import safe_validation_errors, validate_import_package  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="JSON package to validate")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.package.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Unable to read valid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        package = validate_import_package(payload)
    except ValidationError as exc:
        print(
            json.dumps(
                {"valid": False, "errors": safe_validation_errors(exc.errors())},
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )
        return 1

    coordinates = sum(record.geometry is not None for record in package.records)
    source_links = sum(len(record.sources) for record in package.records)
    print(
        json.dumps(
            {
                "valid": True,
                "batch_id": package.batch_id,
                "records": len(package.records),
                "coordinates": coordinates,
                "source_links": source_links,
                "statuses": sorted({record.status for record in package.records}),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
