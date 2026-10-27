"""Fixed synthetic HTTP comparison; unchanged production is deliberately RED."""
from __future__ import annotations

import ast
from collections import Counter
from contextlib import contextmanager
import dis
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import platform
import random
import signal
import statistics
import sys
import tempfile
import threading
import time
from types import CodeType, FunctionType

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.config import Settings
from app.main import create_app, _source_rows
from app.catalogue_quality import batch_observation_points
from fixtures.catalogue_list_sites_before_normalization import install_before
from scripts.benchmark_catalogue import _seed


ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "tests/fixtures/catalogue_list_sites_before_normalization.py"
BEFORE_SHA256 = "8bbc6de11bf441f4e3fcfe55153970c2fe637fd39515023f22bcc09d5c55c6a4"
TOKEN = {"Authorization": "Bearer synthetic-normalization-only"}
SITE_COUNT = 64
LIMIT_BYTES = 16 * 1024 * 1024
LIMIT_OUTPUT = 1024 * 1024
SCHEDULE = (("warmup", "B"), ("warmup", "A")) + tuple(
    ("pilot", arm) for _ in range(5) for arm in "BB"
) + (("warmup", "A"), ("warmup", "B")) + tuple(
    ("measured", arm) for pair in range(10) for arm in ("AB" if pair % 2 == 0 else "BA")
)
# Cases are nested within a member, never independent performance samples.
CASES = (
    ("unfiltered", {}, 200, None, None, True),
    ("empty-q", {"q": ""}, 200, None, None, True),
    ("blank-q", {"q": " \t "}, 200, None, None, True),
    ("empty-kind", {"site_kind": ""}, 200, None, None, True),
    ("blank-kind", {"site_kind": " \t "}, 200, None, None, True),
    ("dense", {"q": " SyNtHeTiC "}, 200, None, None, True),
    ("own-name", {"q": " SYNTHETIC SITE 0003 "}, 200, [3], None, True),
    ("unicode-own", {"q": " STRASSE "}, 200, [63, 64], None, True),
    ("own-rationale", {"q": " RATIONALE ONLY "}, 200, [63], None, True),
    ("own-location", {"q": " LOCATION ONLY "}, 200, [63], None, True),
    ("own-condition", {"q": " CONDITION ONLY "}, 200, [63], None, True),
    ("own-kind", {"q": " ShElTeR "}, 200, list(range(3, 63)), None, True),
    ("source-only", {"q": " FORENSIC LATER "}, 200, [63], None, True),
    ("source-unicode", {"q": " \u00e6\u00f8\u00e5 "}, 200, [63], None, True),
    ("registry-only", {"q": " ARCHIVE REGISTRY "}, 200, [63], None, True),
    ("legacy-excerpt", {"q": " EVIDENCE ONLY "}, 200, [64], None, True),
    ("absent", {"q": " impossible needle "}, 200, [], None, True),
    ("kind-mixed", {"site_kind": " ShElTeR "}, 200, None, None, True),
    ("kind-absent", {"site_kind": " missing kind "}, 200, [], None, True),
    ("combined", {"q": " STRASSE ", "site_kind": " BuNk ", "status": "candidate", "access": "public", "confidence": "high"}, 200, [63], None, True),
    ("include-rejected", {"include_rejected": "true"}, 200, None, None, True),
    ("rejected-status", {"status": "rejected"}, 200, [1], None, True),
    ("null-confidence", {"confidence": "unknown"}, 200, None, None, True),
    ("no-auth", {"q": " STRASSE "}, 401, None, None, False),
    ("auth-before-invalid", {"status": "invalid"}, 401, None, None, False),
    ("q-length", {"q": "x" * 201}, 422, None, None, True),
    ("kind-length", {"site_kind": "x" * 101}, 422, None, None, True),
    ("bad-bool", {"include_rejected": "not-bool"}, 422, None, None, True),
) + tuple(
    (f"invalid-{field}-{index}", {field: value}, 400, None, None, True)
    for field, valid in (("status", "candidate"), ("access", "public"), ("confidence", "high"))
    for index, value in enumerate(("not-valid", " ", valid.upper()))
) + (
    ("validation-order", {"status": "bad", "access": "bad", "confidence": "bad"}, 400, None, None, True),
) + tuple(
    (f"source-{name}", {"q": " STRASSE "}, 200, [63, 64], payload, True)
    for name, payload in (
        ("malformed", "{"), ("duplicate-key", '{"sources":[],"sources":[]}'),
        ("trailing", '{"sources":[]} trailing'), ("wrong-root", "[]"),
        ("missing", "{}"), ("wrong-sources", '{"sources":3}'),
        ("empty-sources", '{"sources":[]}'), ("nonobject-reading", '{"sources":[3,null]}'),
        ("json-null", "null"),
    )
) + (
    ("source-blob-own-match", {"q": " STRASSE "}, 500, None, b"{}", True),
    ("source-blob-absent", {"q": " impossible needle "}, 500, None, b"{}", True),
    ("source-blob-blank-q", {"q": " "}, 200, None, b"{}", True),
    ("source-blob-kind-excluded", {"q": " STRASSE ", "site_kind": " ShElTeR "}, 200, [], b"{}", True),
    ("source-blob-invalid", {"q": " STRASSE ", "status": "bad"}, 400, None, b"{}", True),
    ("source-blob-no-auth", {"q": " STRASSE "}, 401, None, b"{}", False),
)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _list_route(app):
    route, = [r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/sites" and "GET" in r.methods]
    return route


def _before_route(app):
    route = _list_route(app)
    index = app.router.routes.index(route)
    routes = list(app.router.routes)
    original = route.endpoint
    assert isinstance(original, FunctionType)
    original_closure = original.__closure__
    assert original_closure is not None
    cells = {name: cell for name, cell in zip(original.__code__.co_freevars, original_closure, strict=True)}
    assert set(cells) == {"database", "effective_enrichment", "site_summary"}
    app.router.routes.remove(route)
    # The frozen decorator runs with the real globals, guard and closure referents.
    installer = FunctionType(install_before.__code__, original.__globals__)
    frozen = installer(app, **{k: v.cell_contents for k, v in cells.items()}, read_guard=route.dependencies[0].dependency)
    assert isinstance(frozen, FunctionType) and frozen.__closure__ is not None
    assert frozen.__globals__ is original.__globals__
    assert frozen.__code__.co_freevars == original.__code__.co_freevars
    assert all(a.cell_contents is b.cell_contents for a, b in zip(frozen.__closure__, original_closure, strict=True))
    replacement = app.router.routes.pop()
    assert app.router.routes == routes[:index] + routes[index + 1:]
    app.router.routes.insert(index, replacement)
    assert app.router.routes == routes[:index] + [replacement] + routes[index + 1:]
    assert _list_route(app) is replacement
    assert replacement.response_model == route.response_model
    assert replacement.dependencies[0].dependency is route.dependencies[0].dependency
    return frozen


def _seed_sources(database):
    _seed(database, SITE_COUNT)
    record = {"sources": [
        {"title": "Synthetic primary", "source_type": "archive"},
        {"title": "Forensic later \u00c6\u00d8\u00c5", "source_type": "archive"},
    ]}
    raw = _json(record).decode()
    with database.connect() as c:
        c.execute("UPDATE sites SET status='rejected' WHERE id=1")
        c.execute("UPDATE sites SET merged_into_id=3 WHERE id=2")
        c.execute("UPDATE sites SET name='Stra\u00dfe evidence',site_kind='bunker' WHERE id IN (63,64)")
        c.execute("UPDATE sites SET access='public',confidence='high',short_rationale='Rationale only',observed_location_text='Location only',condition='Condition only' WHERE id=63")
        c.execute("INSERT INTO import_batches(batch_id,schema_version,generated_at,created_at,committed_at) VALUES('normalization','1.0','2026-01-01','2026-01-01','2026-01-01')")
        c.execute("INSERT INTO import_records(id,batch_id,external_key,site_id,action,payload_json,created_at) VALUES(1,'normalization','benchmark:0063',63,'created',?,'2026-01-01')", (raw,))
        for source_id, site_id in ((1, 63), (2, 63), (3, 64)):
            c.execute("INSERT INTO sources(id,url,title,source_type,excerpt,created_at,updated_at) VALUES(?,?,?,'archive',?,'2026-01-01','2026-01-01')", (source_id, f"https://example.test/normalization/{source_id}", "Archive registry" if source_id != 3 else "Legacy synthetic", "Evidence only" if source_id == 3 else "Synthetic excerpt"))
            c.execute("INSERT INTO evidence(site_id,source_id,role,created_at) VALUES(?,?,'source','2026-01-01')", (site_id, source_id))
            if site_id == 63:
                c.execute("INSERT INTO evidence_items(site_id,source_id,import_record_id,source_index,excerpt,content_kind,role,provenance_status,created_at) VALUES(63,?,1,?,'Synthetic excerpt','summary','context','import_record','2026-01-01')", (source_id, source_id - 1))
    return raw


def _normalization_sites(endpoint):
    tree = ast.parse(Path(endpoint.__code__.co_filename).read_text())
    node, = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "list_sites" and min([n.lineno] + [d.lineno for d in n.decorator_list]) == endpoint.__code__.co_firstlineno]
    spans = {}
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr not in {"strip", "casefold"}:
            continue
        root = call.func.value
        while isinstance(root, ast.Call) and isinstance(root.func, ast.Attribute):
            root = root.func.value
        if isinstance(root, ast.Name) and root.id in {"q", "site_kind"}:
            spans[(call.lineno, call.end_lineno, call.col_offset, call.end_col_offset)] = (root.id, call.func.attr)
    codes = [endpoint.__code__]
    sites = {}
    for code in codes:
        codes.extend(c for c in code.co_consts if isinstance(c, CodeType))
        for instruction in dis.get_instructions(code):
            p = getattr(instruction, "positions")
            key = (p.lineno, p.end_lineno, p.col_offset, p.end_col_offset)
            if key in spans:
                sites[code, instruction.offset] = spans[key]
    assert sites, "no bytecode sites for real request normalization"
    return sites


def _inventory(root):
    files = list(root.rglob("*"))
    assert not any(p.is_symlink() for p in files), "scratch symlink"
    files = [p for p in files if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    assert len(files) <= 128 and size <= LIMIT_BYTES, (len(files), size)
    return len(files), size


def _written_bytes():
    fields = dict(line.split(": ", 1) for line in Path("/proc/self/io").read_text().splitlines())
    return int(fields["write_bytes"])


@contextmanager
def _deadline():
    assert threading.current_thread() is threading.main_thread()
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise TimeoutError("normalization member exceeded 120 seconds")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _phase(root, arm, profiled):
    app = create_app(Settings(data_dir=root, admin_token="synthetic-normalization-only", app_version="normalization-fixture"))
    endpoint = _before_route(app) if arm == "B" else _list_route(app).endpoint
    assert isinstance(endpoint, FunctionType)
    sites = _normalization_sites(endpoint) if profiled else {}
    counts = Counter()
    threads = set()
    sql = []
    reads = []
    database = app.state.database
    connect = database.connect
    original_thread_profile = threading.getprofile()
    original_main_profile = sys.getprofile()
    original_thread_trace = threading.gettrace()
    original_main_trace = sys.gettrace()
    assert original_thread_profile is None and original_main_profile is None
    assert original_thread_trace is None and original_main_trace is None
    existing_threads = set(threading.enumerate())

    def profile(frame, event, arg):
        if event == "c_call" and (frame.f_code, frame.f_lasti) in sites:
            request, method = sites[frame.f_code, frame.f_lasti]
            if getattr(arg, "__name__", None) == method and isinstance(getattr(arg, "__self__", None), str):
                counts[request + "." + method] += 1
                threads.add(threading.get_ident())
        elif event in {"call", "return"} and frame.f_code in {_source_rows.__code__, batch_observation_points.__code__}:
            reads.append((event, frame.f_code.co_name, frame.f_locals.get("site_id"), _sha(_json(arg)) if event == "return" else None))

    def traced_connect():
        c = connect()
        c.set_trace_callback(sql.append)
        return c

    def source_trace(frame, event, arg):
        if frame.f_code is _source_rows.__code__:
            if event == "exception":
                kind, error, _ = arg
                reads.append(("exception", kind.__module__, kind.__qualname__, str(error)))
            return source_trace
        return None

    semantics = []
    traces = []
    wall_ns = cpu_ns = response_bytes = 0
    try:
        if profiled:
            threading.setprofile(profile)
            threading.settrace(source_trace)
            database.connect = traced_connect
        with TestClient(app, raise_server_exceptions=False) as client:
            if not database.path.exists() or not database.path.stat().st_size:
                raise AssertionError("application startup did not create isolated SQLite")
            with database.connect() as c:
                empty = c.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
            if empty:
                _seed_sources(database)
            # Restore the stored canonical record, including the fixture's two readings.
            with database.connect() as c:
                raw = c.execute("SELECT payload_json FROM import_records WHERE id=1").fetchone()[0]
                seeded_data = {table: [dict(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY rowid")] for table in ("sites", "sources", "evidence", "import_batches", "import_records", "evidence_items", "field_observations")}
            data_sha256 = _sha(_json(seeded_data))
            for label, params, status, expected_ids, malformed, authorized in CASES:
                mutate = label.startswith("source-") and label not in {"source-only", "source-unicode"}
                if mutate:
                    with database.connect() as c:
                        c.execute("UPDATE import_records SET payload_json=? WHERE id=1", (malformed,))
                sql.clear()
                reads.clear()
                try:
                    started_cpu = time.process_time_ns()
                    started_wall = time.perf_counter_ns()
                    response = client.get("/api/sites", params=params, headers=TOKEN if authorized else {}, timeout=10)
                    elapsed = time.perf_counter_ns() - started_wall
                    cpu = time.process_time_ns() - started_cpu
                    if not profiled:
                        wall_ns += elapsed
                        cpu_ns += cpu
                    assert response.status_code == status, (label, response.status_code, response.text[:500])
                    if status == 200:
                        body = response.json()
                        ids = [item["id"] for item in body]
                        if expected_ids is not None:
                            assert ids == expected_ids, (label, ids)
                        if label in {"unfiltered", "empty-q", "blank-q", "empty-kind", "blank-kind", "source-blob-blank-q"}:
                            assert len(ids) == 62 and 1 not in ids and 2 not in ids
                        if label == "include-rejected":
                            assert len(ids) == 63 and 1 in ids and 2 not in ids
                    if label.startswith("invalid-") or label == "validation-order":
                        field = "status" if label == "validation-order" else label.split("-")[1]
                        assert response.json() == {"detail": f"invalid site {field}"}
                    response_bytes += len(response.content)
                    headers = dict(response.headers)
                    headers.pop("x-request-id", None)
                    semantics.append((label, status, response.content, headers))
                    if profiled:
                        if status in {400, 401, 422}:
                            assert not sql and not reads, label
                        elif label in {"unfiltered", "empty-q", "blank-q", "source-blob-blank-q", "kind-absent"}:
                            assert not any(r[1] == "_source_rows" for r in reads), label
                        if any(r[1] == "_source_rows" for r in reads):
                            assert reads[0][:2] == ("call", "batch_observation_points"), label
                        if status == 500:
                            assert ("exception", "builtins", "AttributeError", "'bytes' object has no attribute 'encode'") in reads, (label, reads)
                        traces.append((label, len(sql), _sha(_json(sql)), len(reads), _sha(_json(reads))))
                    _inventory(root)
                finally:
                    if mutate:
                        with database.connect() as c:
                            c.execute("UPDATE import_records SET payload_json=? WHERE id=1", (raw,))
    finally:
        database.connect = connect
        threading.setprofile(original_thread_profile)
        threading.settrace(original_thread_trace)
        assert sys.getprofile() is original_main_profile
        assert sys.gettrace() is original_main_trace
        for thread in set(threading.enumerate()) - existing_threads:
            thread.join(timeout=2)
            assert not thread.is_alive(), "owned client/profiler thread survived shutdown"
    if profiled:
        assert threads and threading.get_ident() not in threads
        assert all(counts[key] > 0 for key in ("q.strip", "q.casefold", "site_kind.strip", "site_kind.casefold")), counts
    return semantics, traces, {"normalizations": sum(counts.values()), "by_operation": dict(counts), "profiled_worker_threads": len(threads), "wall_ns": wall_ns, "cpu_ns": cpu_ns, "response_bytes": response_bytes, "route_function_sha256": _sha(inspect.getsource(endpoint).encode()), "data_sha256": data_sha256}


def _timing(before, after, noise):
    # Arithmetic frozen from portfolio tools/decisions.py (seed 1127, 2000 resamples).
    assert all(x > 0 for x in before) and all(x >= 0 for x in after)
    relative = [(a - b) / a for a, b in zip(before, after, strict=True)]
    rng = random.Random(1127)
    bootstrap = sorted(statistics.median(rng.choices(relative, k=len(relative))) for _ in range(2000))
    return {"gain": statistics.median(relative), "threshold": max(.05, 2 * noise), "bootstrapLower95": bootstrap[49]}


# /proc/self/io byte accounting is qualified only on hosted Linux CPython 3.12.
@pytest.mark.skipif(sys.platform != "linux", reason="/proc/self/io write accounting requires hosted Linux")
def test_catalogue_query_normalization_performance(tmp_path, capsys):
    with capsys.disabled():
        assert platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 12)
        assert sys.platform == "linux", "bounded write accounting requires hosted Linux /proc"
        assert _sha(BEFORE.read_bytes()) == BEFORE_SHA256, "frozen before source changed"
        root = tmp_path.resolve(strict=True)
        assert tmp_path.is_absolute() and root == tmp_path.absolute() and not tmp_path.is_symlink()
        runtime = {"python": sys.version, "platform": platform.platform(), "dependencies": {name: importlib.metadata.version(name) for name in ("fastapi", "starlette", "anyio", "pydantic", "httpx2", "pytest")}}
        workload = [[label, params, status, ids, {"sqlite_blob_hex": malformed.hex()} if isinstance(malformed, bytes) else malformed, authorized] for label, params, status, ids, malformed, authorized in CASES]
        common = {path: _sha((ROOT / path).read_bytes()) for path in ("app/db.py", "app/config.py", "app/contracts.py", "app/catalogue_quality.py", "tests/test_import_api.py", "tests/started_client.py", "tests/conftest.py", "scripts/benchmark_catalogue.py", "requirements.txt", "requirements-dev.txt")}
        identity = {"test": _sha(Path(__file__).read_bytes()), "before_fixture": _sha(BEFORE.read_bytes()), "candidate_source": _sha((ROOT / "app/main.py").read_bytes()), "common": common, "data": _sha(_json([SITE_COUNT, workload])), "runtime": runtime}
        records = []
        golden = None
        golden_traces = None
        golden_data = None
        for index, (stage, arm) in enumerate(SCHEDULE):
            written = _written_bytes()
            with _deadline():
                with tempfile.TemporaryDirectory(prefix=f"member-{index:02d}-", dir=root) as temporary:
                    scratch = Path(temporary).resolve(strict=True)
                    assert scratch.parent == root
                    observed, traces, primary = _phase(scratch, arm, True)
                    uninstrumented, _, timing = _phase(scratch, arm, False)
                    assert primary["route_function_sha256"] == timing["route_function_sha256"]
                    assert primary["data_sha256"] == timing["data_sha256"]
                    assert observed == uninstrumented, "profiling changed complete HTTP behavior"
                    if golden is None:
                        golden, golden_traces = observed, traces
                        golden_data = primary["data_sha256"]
                    assert primary["data_sha256"] == golden_data, "synthetic data identity changed"
                    assert observed == golden, "complete response/status/header/order mismatch"
                    assert traces == golden_traces, "SQL/source/prefetch order or work changed"
                    files, size = _inventory(scratch)
                    assert _written_bytes() - written <= LIMIT_BYTES, "member exceeded actual write budget"
                    record = {"index": index, "stage": stage, "arm": arm, "identity": identity, "route_function_sha256": primary["route_function_sha256"], "data_sha256": primary["data_sha256"], "metrics": {"normalizations": primary["normalizations"], "wall_ns": timing["wall_ns"], "cpu_ns": timing["cpu_ns"], "response_bytes": timing["response_bytes"], "files": files, "scratch_bytes": size, "sql_statements": sum(t[1] for t in traces), "read_events": sum(t[3] for t in traces)}, "by_operation": primary["by_operation"], "profiled_worker_threads": primary["profiled_worker_threads"], "semantic_sha256": _sha(_json([(label, status, _sha(body), headers) for label, status, body, headers in observed])), "traces": traces}
                assert not scratch.exists(), "owned member scratch was not removed"
            data = _json(record)
            artifact = root / f"member-{index:02d}.json"
            line = f"{artifact} ".encode() + data + b"\n"
            assert len(line) <= LIMIT_OUTPUT
            artifact.write_bytes(data + b"\n")
            print(line.decode(), end="")
            records.append(record)
            _inventory(root)
        pilot = [r for r in records if r["stage"] == "pilot"]
        measured = [r for r in records if r["stage"] == "measured"]
        decisions = {}
        for metric in ("normalizations", "wall_ns", "cpu_ns"):
            before = [next(r["metrics"][metric] for r in measured[i:i + 2] if r["arm"] == "B") for i in range(0, 20, 2)]
            after = [next(r["metrics"][metric] for r in measured[i:i + 2] if r["arm"] == "A") for i in range(0, 20, 2)]
            noise = statistics.median(abs(pilot[i]["metrics"][metric] - pilot[i + 1]["metrics"][metric]) / pilot[i]["metrics"][metric] for i in range(0, 10, 2))
            if metric == "normalizations":
                assert len(set(before)) == len(set(after)) == 1
            decisions[metric] = _timing(before, after, noise)
        for metric in ("response_bytes", "files", "scratch_bytes", "sql_statements", "read_events"):
            values = [r["metrics"][metric] for r in records]
            assert len(set(values)) == 1, (metric, values)
        regressions = [metric for metric in ("wall_ns", "cpu_ns") if decisions[metric]["gain"] < -decisions[metric]["threshold"]]
        primary = decisions["normalizations"]
        improved = primary["gain"] > primary["threshold"] and primary["bootstrapLower95"] > 0
        result = {"identity": identity, "decisions": decisions, "secondary_regressions": regressions, "members": len(records), "status": "PASS" if improved and not regressions else "NO_PROVEN_GAIN"}
        artifact = root / "decision.json"
        data = _json(result)
        line = f"{artifact} ".encode() + data + b"\n"
        assert len(line) <= LIMIT_OUTPUT
        artifact.write_bytes(data + b"\n")
        print(line.decode(), end="")
        assert not regressions, regressions
        assert improved, "planned RED: strict measured normalization improvement is absent"
