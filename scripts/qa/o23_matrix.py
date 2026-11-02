"""Frozen Bunkerkartet O23/U05 acceptance case matrix, result validation and parity.

Pure standard library; importing this module never starts the app or a browser.
Result files are untrusted data: they are parsed, never executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "o23-result-v1"
SIZES = {"small": 100, "large": 1000}  # sizes of scripts/benchmark_catalogue.py SIZES
MAX_RESULT_BYTES = 1024 * 1024
STATUSES = {"PASS", "FAIL"}
# Response headers that carry semantics; X-Request-ID and Date vary per request.
HEADER_ALLOWLIST = ("content-type", "content-encoding", "content-length", "retry-after", "cache-control", "vary")

FILTERS = {  # catalogue filter cohorts over the seeded names "Synthetic site NNNN"
    "unfiltered": {},
    "sparse": {"q": "0003"},
    "dense": {"q": "synthetic"},
    "kind": {"site_kind": "shelter"},
    "nomatch": {"q": "no-such-needle"},
}
ENCODINGS = ("identity", "gzip")
ROUTE_API = (
    "route.create", "route.persist-restart", "route.list", "route.auth-none", "route.auth-wrong",
    "route.missing-404", "route.invalid-payload", "route.invalid-site", "route.lease-expired",
    "route.lease-pending", "route.erased-410",
)
BROWSER = (
    "browser.cold-load", "browser.warm-reload", "browser.filter-sparse", "browser.filter-dense",
    "browser.route-view-cold", "browser.route-view-warm",
)
ADVERSE = (
    "adverse.empty-catalogue-api", "adverse.unavailable-db-api", "adverse.invalid-token-browser",
    "adverse.denied-storage-browser", "adverse.offline-revisit-browser", "adverse.empty-catalogue-browser",
)
# Original-matrix items satisfied by existing tests, not by this fixture.
COVERED_ELSEWHERE = {
    "malformed-rows": "tests/test_o23_malformed_sources.py (9 JSON and 6 SQLite BLOB payload cases); historical origin PR113 blob b248cede6935cefbb05ba830ec4dffbbba8b24c1:tests/test_catalogue_query_normalization_performance.py (161de15ab2eb32ef29d4e2b64de6769ac2ca1687), whose performance experiment is not present or rerun in this PR111 stack",
    "automatic-route-expiry": "not an app feature: only the 60 s pending lease and explicit owner erasure exist",
    "reader-credential-expiry": "requires pilot_readers_enabled; not part of the original O23 matrix",
}


def cases() -> list[dict[str, str]]:
    out = []
    for size in SIZES:
        for name in FILTERS:
            for enc in ENCODINGS:
                out.append({"id": f"api.{size}.list-{name}.{enc}", "unit": f"api-{size}", "size": size})
        for rid in ROUTE_API:
            out.append({"id": f"api.{size}.{rid}", "unit": f"api-{size}", "size": size})
        for bid in BROWSER:
            out.append({"id": f"{bid.split('.')[0]}.{size}.{bid.split('.', 1)[1]}", "unit": f"browser-{size}", "size": size})
    for aid in ADVERSE:
        out.append({"id": f"{aid.split('.')[0]}.small.{aid.split('.', 1)[1]}", "unit": "adverse-small", "size": "small"})
    return out


UNITS = tuple(dict.fromkeys(case["unit"] for case in cases()))


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


# The fixture server pins the app's generated-timestamp source (app.db.now_iso and every module that
# imported it) to this authored instant, so created_at/updated_at/event times are deterministic and
# no response field has to be dropped before comparison. Lease expiry uses time.time() with explicit
# authored expires_at values and is not touched.
FIXED_NOW = "2026-01-01T12:00:00+00:00"


def freeze_clock(modules, fixed: str = FIXED_NOW) -> int:
    """Replace now_iso on every given module that has one; returns how many were pinned."""
    count = 0
    for module in modules:
        if callable(getattr(module, "now_iso", None)):
            module.now_iso = lambda fixed=fixed: fixed
            count += 1
    return count


_SECRET_ASSIGNMENT = re.compile(r"(?i)\b(token|key|secret|password|authorization)(\s*[=:]\s*)\S+")


def redact(text: str, *, secrets=(), paths=None, limit: int = 512) -> str:
    """Bounded, single-line, redacted diagnostic text; failure evidence is data, never markup."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    for path, label in (paths or {}).items():
        if path:
            text = text.replace(path, label)
    text = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = re.sub(r"/run/o23-[A-Za-z0-9]+", "<scratch>", text)
    return "".join(ch if ch.isprintable() else " " for ch in text)[:limit]


def validate_result(raw: bytes, unit: str) -> dict:
    """Strict parse of an untrusted result document; raises ValueError on any deviation."""
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("result exceeds 1 MiB stream bound")
    seen: set[str] = set()

    def no_duplicates(pairs):
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate JSON key")
        return dict(pairs)

    def reject_constant(name):
        raise ValueError("non-finite number")

    doc = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    if not isinstance(doc, dict) or set(doc) != {"schema", "unit", "python", "cases"}:
        raise ValueError("unexpected result keys")
    if doc["schema"] != SCHEMA or doc["unit"] != unit or not isinstance(doc["python"], str):
        raise ValueError("schema/unit mismatch")
    expected = [case["id"] for case in cases() if case["unit"] == unit]
    if not isinstance(doc["cases"], list) or [c.get("id") if isinstance(c, dict) else None for c in doc["cases"]] != expected:
        raise ValueError("case ids/order differ from the frozen matrix")
    for case in doc["cases"]:
        if set(case) != {"id", "status", "checks", "record"} or case["status"] not in STATUSES:
            raise ValueError(f"bad case shape: {case.get('id')}")
        checks = case["checks"]
        if not isinstance(checks, dict) or not checks or any(type(v) is not bool for v in checks.values()):
            raise ValueError(f"bad checks: {case['id']}")
        if (case["status"] == "PASS") != all(checks.values()):
            raise ValueError(f"status contradicts checks: {case['id']}")
        if case["id"] in seen or not isinstance(case["record"], dict):
            raise ValueError("duplicate id or record")
        seen.add(case["id"])
    return doc


def parity(baseline: dict, candidate: dict) -> list[str]:
    """Return differences between two validated results; empty list means full ordered parity.

    Compares status, every check and the whole canonical record (status codes, error bodies,
    ordered payload digests, wire bytes, encodings, privacy observations). Timing never enters
    a record, so none is compared here.
    """
    diffs = []
    if baseline["unit"] != candidate["unit"]:
        return ["unit differs"]
    for left, right in zip(baseline["cases"], candidate["cases"], strict=True):
        for part in ("status", "checks", "record"):
            if canonical(left[part]) != canonical(right[part]):
                diffs.append(f"{left['id']}: {part} differs")
    return diffs


def self_test() -> None:
    matrix = cases()
    ids = [case["id"] for case in matrix]
    assert len(ids) == len(set(ids)) and len(ids) == 2 * (5 * 2 + 11 + 6) + 6, len(ids)
    assert set(UNITS) == {"api-small", "api-large", "browser-small", "browser-large", "adverse-small"}

    def good(unit):
        return {"schema": SCHEMA, "unit": unit, "python": "3.12.0", "cases": [
            {"id": c["id"], "status": "PASS", "checks": {"ok": True}, "record": {"status": 200, "sha": "a"}}
            for c in matrix if c["unit"] == unit]}

    base = good("api-small")
    raw = json.dumps(base).encode()
    assert validate_result(raw, "api-small")["unit"] == "api-small"
    assert parity(base, json.loads(raw)) == []
    changed = json.loads(raw)
    changed["cases"][3]["record"]["sha"] = "b"
    assert len(parity(base, changed)) == 1  # a real difference is reported, not averaged away
    malformed = {
        "truncated": raw[:-5],
        "duplicate key": b'{"schema":"x","schema":"y"}',
        "nan": raw.replace(b'"status": 200', b'"status": NaN', 1),
        "oversize": b" " * (MAX_RESULT_BYTES + 1),
        "wrong unit": raw,
    }
    for label, data in malformed.items():
        try:
            validate_result(data, "api-large" if label == "wrong unit" else "api-small")
        except ValueError:
            continue
        raise AssertionError(f"accepted malformed result: {label}")
    contradictory = json.loads(raw)
    contradictory["cases"][0]["checks"] = {"ok": False}
    for mutate in (lambda d: d["cases"].reverse(), lambda d: d["cases"].pop(), lambda d: d.update(extra=1)):
        doc = json.loads(raw)
        mutate(doc)
        try:
            validate_result(json.dumps(doc).encode(), "api-small")
        except ValueError:
            continue
        if doc != json.loads(raw):
            raise AssertionError("accepted mutated result")
    try:
        validate_result(json.dumps(contradictory).encode(), "api-small")
    except ValueError:
        pass
    else:
        raise AssertionError("accepted status that contradicts checks")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="print the frozen case matrix as JSON")
    group.add_argument("--self-test", action="store_true", help="pure matrix/parser self-test (not app or browser QA)")
    group.add_argument("--compare", nargs=3, metavar=("UNIT", "BASELINE", "CANDIDATE"))
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps({"cases": cases(), "units": UNITS, "coveredElsewhere": COVERED_ELSEWHERE}, indent=1))
    elif args.self_test:
        self_test()
        print("o23_matrix self-test ok")
    else:
        unit, left, right = args.compare
        docs = [validate_result(Path(p).read_bytes(), unit) for p in (left, right)]
        diffs = parity(*docs)
        print(json.dumps({"parity": not diffs, "differences": diffs}, indent=1))
        return 0 if not diffs else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
