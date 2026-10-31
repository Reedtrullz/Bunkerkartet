"""Pure source checks for the O23 repair (G1-G3). Nothing here starts the app, Chromium or a namespace.

Run directly: python tests/test_o23_repair_source.py   (pytest also collects the test_* functions).
O23_QA_DIR may point at another copy of scripts/qa (used to replay these checks against the old helpers).
"""
import contextlib
import importlib
import io
import json
import os
import signal
import sys
import tempfile
import types
from pathlib import Path

QA = Path(os.environ.get("O23_QA_DIR") or Path(__file__).resolve().parents[1] / "scripts/qa")
sys.path.insert(0, str(QA))
adm = importlib.import_module("o23_linux_admission")
acc = importlib.import_module("o23_acceptance")
matrix = importlib.import_module("o23_matrix")


def _doc(unit="api-small", status="PASS"):
    cases = [{"id": c["id"], "status": status, "checks": {"ok": status == "PASS"}, "record": {"s": 200}}
             for c in matrix.cases() if c["unit"] == unit]
    return json.dumps({"schema": matrix.SCHEMA, "unit": unit, "python": "3.12.0", "cases": cases}, sort_keys=True).encode()


def _run(output=b"", returncode=0, overflow=False, wrapper_deadline=False, survivors=0, scratch_removed=True, elapsed_s=1.0):
    return {"returncode": returncode, "output": output, "overflow": overflow, "wrapper_deadline": wrapper_deadline,
            "elapsed_s": elapsed_s, "survivors": survivors, "scratch_removed": scratch_removed}


@contextlib.contextmanager
def _stub(responder):
    original = adm.run_contained
    adm.run_contained = lambda inner, nonce, **kw: responder(inner, kw)
    try:
        yield
    finally:
        adm.run_contained = original


def _unit_status(**overrides):
    with tempfile.TemporaryDirectory() as tmp, _stub(lambda inner, kw: _run(_doc() + b"\n", **overrides)):
        return adm.run_unit("api-small", Path(tmp))["status"]


def test_g2_unit_passes_only_when_everything_is_clean():
    assert _unit_status() == "PASS"  # positive parse preserved


def test_g2_unit_rejects_pass_shaped_output_with_bad_process_state():
    for label, overrides in (("nonzero exit", {"returncode": 1}), ("signal exit", {"returncode": -9}),
                             ("leftover child", {"survivors": 1}), ("scratch left", {"scratch_removed": False}),
                             ("cap overflow", {"overflow": True}), ("wrapper deadline", {"wrapper_deadline": True})):
        assert _unit_status(**overrides) == "FAIL", label


def test_g2_unit_rejects_partial_and_empty_documents():
    for label, output in (("empty", b""), ("truncated", _doc()[:-9] + b"\n"), ("failed case", _doc(status="FAIL") + b"\n")):
        with tempfile.TemporaryDirectory() as tmp, _stub(lambda inner, kw, o=output: _run(o)):
            assert adm.run_unit("api-small", Path(tmp))["status"] == "FAIL", label


def _control(name_part, responders):
    """Run controls() with canned run results; returns the pass flag of the one control matching name_part."""
    def responder(inner, kw):
        for const, make in responders.items():
            if inner[2:3] == [getattr(adm, const, object())]:
                return make()
        return _run(b"{}\n")
    with tempfile.TemporaryDirectory() as home, _stub(responder):
        report = adm.controls(home=Path(home))
    hits = [c for c in report if name_part in c["control"]]
    assert len(hits) == 1, (name_part, [c["control"] for c in report])
    return hits[0]["pass"]


EGRESS_OK = b'{"192.0.2.1": "TimeoutError", "1.1.1.1": "OSError", "loopback": "OK"}\n'
OVER_OK = b'{"errno": 28, "mib": 40}\n'


def test_g2_controls_reject_bad_exit_overflow_and_missing_teardown():
    assert _control("egress", {"EGRESS": lambda: _run(EGRESS_OK)}) is True
    for label, overrides in (("exit", {"returncode": 1}), ("overflow", {"overflow": True}), ("scratch", {"scratch_removed": False}),
                             ("survivor", {"survivors": 1}), ("deadline", {"wrapper_deadline": True})):
        assert _control("egress", {"EGRESS": lambda o=overrides: _run(EGRESS_OK, **o)}) is False, label
        assert _control("ENOSPC", {"OVERLIMIT": lambda o=overrides: _run(OVER_OK, **o)}) is False, label
    assert _control("ENOSPC", {"OVERLIMIT": lambda: _run(OVER_OK)}) is True


def test_g2_negative_controls_assert_their_precise_failure():
    plain = lambda **k: _run(b"", **k)  # noqa: E731
    for ok in (plain(returncode=137, elapsed_s=3.5), plain(returncode=124, elapsed_s=3.07)):
        assert _control("deadline", {"SLEEP": lambda b=ok: b}) is True
    for bad in (plain(returncode=1, elapsed_s=3.5), plain(returncode=0, elapsed_s=3.5), plain(returncode=137, elapsed_s=3.5, survivors=1),
                plain(returncode=137, elapsed_s=3.5, scratch_removed=False), plain(returncode=124, elapsed_s=0.1),
                plain(returncode=124, elapsed_s=15), plain(returncode=124, elapsed_s=3.5, overflow=True),
                plain(returncode=124, elapsed_s=3.5, wrapper_deadline=True)):
        assert _control("deadline", {"SLEEP": lambda b=bad: b}) is False
    flood = lambda **k: _run(b"x", **{"overflow": True, "returncode": -15, **k})  # noqa: E731
    assert _control("1 MiB", {"FLOOD": lambda: flood()}) is True
    assert _control("1 MiB", {"FLOOD": lambda: flood(returncode=120, elapsed_s=0.08)}) is True
    for bad in (flood(overflow=False), flood(returncode=0), flood(returncode=1), flood(scratch_removed=False), flood(survivors=1),
                flood(returncode=120, elapsed_s=20), flood(returncode=120, wrapper_deadline=True), flood(returncode=120, overflow=False)):
        assert _control("1 MiB", {"FLOOD": lambda b=bad: b}) is False
    assert _control("detached", {"SURVIVOR": lambda: plain(returncode=0)}) is True
    assert _control("detached", {"SURVIVOR": lambda: plain(returncode=2)}) is False


def test_g2_required_controls_must_all_be_present():
    report = [{"control": name, "pass": True} for name in adm.REQUIRED_CONTROLS]
    assert adm.controls_complete(report) is True
    assert adm.controls_complete(report[:-1]) is False
    assert adm.controls_complete(report + [{"control": "extra", "pass": True}]) is False
    assert adm.controls_complete([{**c, "pass": False} if i == 0 else c for i, c in enumerate(report)]) is False


def test_g1_isolation_judges_reject_leaks():
    private_ok = {"home_canary": "ENOENT", "git_head": "ENOTDIR", "outside": {"checkout": 30, "home": 30, "host_dir": "ABSENT"}}
    host_ok = {"canary_intact": True, "outside_entries": []}
    assert adm.judge_private(private_ok, host_ok) is True
    for key, value in (("home_canary", "READ"), ("git_head", "READ")):
        assert adm.judge_private({**private_ok, key: value}, host_ok) is False, key
    for key, value in (("checkout", "WRITABLE"), ("home", "WRITABLE"), ("host_dir", "PRESENT")):
        assert adm.judge_private({**private_ok, "outside": {**private_ok["outside"], key: value}}, host_ok) is False, key
    assert adm.judge_private(private_ok, {**host_ok, "outside_entries": ["leak"]}) is False
    assert adm.judge_private(private_ok, {**host_ok, "canary_intact": False}) is False
    zero = {"CapInh": "0", "CapPrm": "0", "CapEff": "0", "CapBnd": "0", "CapAmb": "0"}
    privs_ok = {"nnp": 1, "caps": zero, "sudo": "DENIED", "setuid0": "EPERM", "mount": "DENIED"}
    assert adm.judge_privs(privs_ok) is True
    assert adm.judge_privs({**privs_ok, "nnp": 0}) is False
    assert adm.judge_privs({**privs_ok, "caps": {**zero, "CapBnd": "1"}}) is False
    assert adm.judge_privs({**privs_ok, "caps": {**zero, "CapBnd": "000001ffffffffff"}}) is False
    assert adm.judge_privs({**privs_ok, "sudo": "OK"}) is False
    assert adm.judge_privs({**privs_ok, "mount": "OK"}) is False


def test_g1_private_control_fails_through_controls_when_canary_is_readable():
    leak = b'{"home_canary": "READ", "git_head": "ENOENT", "outside": {"checkout": 30, "home": 30, "host_dir": "ABSENT"}}\n'
    good = leak.replace(b'"READ"', b'"ENOENT"')
    assert _control("private HOME", {"PRIVATE": lambda: _run(good)}) is True
    assert _control("private HOME", {"PRIVATE": lambda: _run(leak)}) is False
    assert _control("private HOME", {"PRIVATE": lambda: _run(good, returncode=1)}) is False


def test_g1_budget_leaves_headroom_and_artifacts_are_bounded():
    assert adm.SCRATCH_BYTES < 64 * 1024 * 1024 and adm.SCRATCH_BYTES + adm.ARTIFACT_BYTES <= 64 * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a").write_bytes(b"x")
        assert adm.artifacts_within_budget(Path(tmp)) is True
        with open(Path(tmp) / "big", "wb") as handle:
            handle.truncate(adm.ARTIFACT_BYTES + 1)
        assert adm.artifacts_within_budget(Path(tmp)) is False
    assert _control("ENOSPC", {"OVERLIMIT": lambda: _run(b'{"errno": 28, "mib": 64}\n')}) is False  # beyond the 48 MiB bound


def test_g1_failure_evidence_is_bounded_and_redacted():
    text = "boom /run/o23-ab12/work/data TOKEN=o23-audit-only password: hunter2 " + "A" * 5000 + "\nINJECT"
    out = matrix.redact(text, secrets=["o23-audit-only"], limit=400)
    assert len(out) <= 400 and "o23-audit-only" not in out and "hunter2" not in out and "/run/o23-ab12" not in out and "\n" not in out
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "server.log"
        log.write_bytes(b"x" * 10000 + b"TRACEBACK key=abc123\n")
        tail = acc.log_tail(log)
        assert len(tail) <= 4096 and "TRACEBACK" in tail
        assert "abc123" not in matrix.redact(tail)
    seen = []
    failed = acc.case("x", lambda: (_ for _ in ()).throw(KeyError("cause")), emit=lambda kind, detail: seen.append(detail))
    assert failed["record"] == {"error": "KeyError"} and seen and "cause" in seen[0]


def _resp(body):
    return {"status": 200, "headers": {}, "raw": json.dumps(body).encode()}


def test_g3_semantic_timestamps_are_not_scrubbed():
    base = {"id": 1, "created_at": "2026-01-01T12:00:00+00:00", "expires_at": 4000000000.0, "reviewed_at": "2026-01-01",
            "time": "12:00", "items": [{"observed_at": "2026-01-01", "v": 1}]}
    original = {k: v for k, v in acc.summary(_resp(base)).items() if k.endswith("body_sha256")}
    assert original
    for path in (("created_at",), ("expires_at",), ("reviewed_at",), ("time",), ("items", 0, "observed_at")):
        mutated = json.loads(json.dumps(base))
        target = mutated
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = "MUTATED"
        changed = {k: v for k, v in acc.summary(_resp(mutated)).items() if k.endswith("body_sha256")}
        assert all(changed[k] != original[k] for k in original), path
    err = {"detail": "x <time>y</time>"}
    assert acc.error_body(_resp(err)) == err


def test_g3_raw_wire_evidence_retained_and_body_is_full():
    body = {"b": [3, 1, 2], "created_at": "2026-01-01T12:00:00+00:00"}
    out = acc.summary(_resp(body))
    assert out["wire_sha256"] and out["decoded_sha256"] and out["wire_bytes"] == len(json.dumps(body).encode())
    assert out["body_sha256"] == matrix.digest(body)
    assert acc.summary(_resp({**body, "b": [1, 2, 3]}))["body_sha256"] != out["body_sha256"]  # order is semantic


def test_g3_frozen_clock_makes_generated_timestamps_comparable():
    class Fake:
        def __init__(self, second):
            self.now_iso = lambda: f"2026-10-29T10:00:{second}+00:00"

    first, second = Fake(10), Fake(11)
    assert first.now_iso() != second.now_iso()  # unfrozen generated volatility
    assert matrix.freeze_clock([first, second, object()]) == 2
    one, two = acc.summary(_resp({"created_at": first.now_iso()})), acc.summary(_resp({"created_at": second.now_iso()}))
    assert one == two  # raw wire digest and body digest agree without dropping any field
    assert one["body_sha256"] == matrix.digest({"created_at": matrix.FIXED_NOW})
    doc = {"unit": "u", "cases": [{"id": "a", "status": "PASS", "checks": {"ok": True}, "record": one}]}
    shifted = json.loads(json.dumps(doc))
    shifted["cases"][0]["record"] = acc.summary(_resp({"created_at": "2027-01-01T00:00:00+00:00"}))
    assert matrix.parity(doc, doc) == [] and matrix.parity(doc, shifted) == ["a: record differs"]


def test_private_program_probes_original_host_home_not_scratch_home():
    """Run the real PRIVATE program against in-memory fakes; no real file, credential or /tmp is touched."""
    import builtins
    import io
    host_home, scratch_home, root = "/fake/host-home", "/fake/run/o23-x/home", "/fake/checkout"
    attempts = []

    class FakeFile:
        def read(self, n=-1):
            return b""

        def write(self, data):
            return len(data)

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def fake_open(path, mode="r", *a, **k):
        path = str(path)
        attempts.append((path, mode))
        if path.startswith((host_home, root)) and "w" in mode:
            raise OSError(30, "EROFS")  # read-only original home and checkout
        if path.endswith(("canary", "HEAD")):
            raise OSError(2, "ENOENT")
        return FakeFile()  # scratch home and /tmp style paths are writable in the fake

    real = (builtins.open, os.unlink, os.path.exists, os.environ.get("HOME"), sys.argv, sys.stdout)
    sink = io.StringIO()
    try:
        builtins.open, os.unlink, os.path.exists = fake_open, lambda p: None, lambda p: False
        os.environ["HOME"] = scratch_home
        sys.argv = ["-c", host_home + "/canary", root, "/fake/outside", "marker", host_home]
        sys.stdout = sink
        exec(compile(adm.PRIVATE, "<PRIVATE>", "exec"), {"__name__": "__private__"})
    finally:
        builtins.open, os.unlink, os.path.exists = real[:3]
        sys.argv, sys.stdout = real[4], real[5]
        if real[3] is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = real[3]
    seen = json.loads(sink.getvalue())
    probed = [p for p, m in attempts if "w" in m]
    assert host_home + "/o23-write-probe" in probed
    assert not any(p.startswith(scratch_home) for p in probed)
    assert seen["outside"]["home"] == 30 and seen["outside"]["checkout"] == 30
    assert adm.judge_private(seen, {"canary_intact": True, "outside_entries": []})
    wrong = dict(seen, outside=dict(seen["outside"], home="WRITABLE"))
    assert not adm.judge_private(wrong, {"canary_intact": True, "outside_entries": []})


def test_required_remount_failure_is_fail_closed_before_app():
    """Execute the exact guard line with a shell-function mount that fails; no real mount, sudo or namespace."""
    import subprocess
    line = next(l for l in adm.SCRIPT.splitlines() if "remount,bind,ro" in l)
    program = "mount(){ return 1; }\nmp=/fake/mountpoint\n" + line + "\necho APP_LAUNCHED\n"
    done = subprocess.run(["/bin/sh", "-c", program], capture_output=True, text=True, timeout=10)
    assert done.returncode == 97, done.returncode
    assert "remount-failed /fake/mountpoint" in done.stderr and len(done.stderr) < 300
    assert "APP_LAUNCHED" not in done.stdout + done.stderr


def _scalar_ids(argv):
    start = argv.index("o23")
    return argv[start + 2], argv[start + 3]


def test_outer_argv_uid_gid_are_scalar_for_default_and_override_paths():
    original = adm.uid_gid
    adm.uid_gid = lambda: (1001, 1002)
    try:
        assert _scalar_ids(adm.outer_argv(["/py"], "n")) == ("1001", "1002")  # defaults
        assert _scalar_ids(adm.outer_argv(["/py"], "n", uid=7, gid=8)) == ("7", "8")  # both overridden
        assert _scalar_ids(adm.outer_argv(["/py"], "n", uid=7)) == ("7", "1002")  # uid only
        assert _scalar_ids(adm.outer_argv(["/py"], "n", gid=8)) == ("1001", "8")  # gid only
    finally:
        adm.uid_gid = original


def _resp_status(body, status=200):
    return {"status": status, "headers": {}, "raw": json.dumps(body).encode()}


class _FakeServer:
    """Stands in for the app server: stop/start optionally mutate what the detail GET returns."""

    def __init__(self, detail, after_restart=None):
        self.detail, self.after_restart, self.events = detail, after_restart, []

    def stop(self):
        self.events.append("stop")

    def start(self):
        self.events.append("start")
        if self.after_restart is not None:
            self.detail = self.after_restart


def _persist(server, route_id=7):
    """Run the fixture's persist step with a fake request(); returns (checks, requested paths)."""
    seen, original = [], acc.request
    acc.request = lambda srv, method, path, **kw: (seen.append((method, path)), srv.detail)[1]
    try:
        checks, _record = acc.persist_roundtrip(server, route_id)
    finally:
        acc.request = original
    return checks, seen


def test_persist_passes_on_stable_detail_even_though_create_shape_differs():
    detail = _resp_status({"id": 7, "points": [1, 2], "created_at": "2026-01-01T00:00:00+00:00", "extra": "only-in-detail"})
    server = _FakeServer(detail)
    checks, seen = _persist(server)
    assert all(checks.values()), checks
    assert server.events == ["stop", "start"]  # the real restart is retained
    assert seen == [("GET", "/api/routes/7")] * 2  # same endpoint before and after


def test_persist_fails_on_mutated_detail_after_restart():
    detail = _resp_status({"id": 7, "points": [1, 2]})
    for label, mutated in (("changed field", _resp_status({"id": 7, "points": [1, 3]})),
                           ("reordered list", _resp_status({"id": 7, "points": [2, 1]})),
                           ("dropped field", _resp_status({"id": 7})),
                           ("added field", _resp_status({"id": 7, "points": [1, 2], "x": 1})),
                           ("status", _resp_status({"id": 7, "points": [1, 2]}, status=404)),
                           ("malformed body", {"status": 200, "headers": {}, "raw": b"{not json"})):
        checks, _ = _persist(_FakeServer(detail, after_restart=mutated))
        assert not all(checks.values()), label


def test_persist_fails_on_bad_status_before_restart_and_non_integer_id():
    bad = _resp_status({"id": 7}, status=500)
    checks, _ = _persist(_FakeServer(bad))  # identical before/after, but not HTTP 200
    assert checks["identical_after_restart"] and not all(checks.values())
    assert not all(_persist(_FakeServer(_resp_status({"id": 7})), route_id=None)[0].values())


def test_browser_response_callback_is_a_python_handler_delivering_the_exact_object():
    class FakePage:
        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    class FakeContext:
        def __init__(self):
            self.browser = types.SimpleNamespace(new_browser_cdp_session=lambda: self)

        def on(self, event, handler):
            self.crash_handler = handler

        def send(self, method, params):
            assert method == "Target.setDiscoverTargets" and params == {"discover": True}

        def set_default_timeout(self, _ms):
            pass

        def route(self, *_a):
            pass

        def new_page(self):
            return page

    page = FakePage()
    playwright = type("P", (), {"chromium": type("C", (), {"launch_persistent_context": lambda *_a, **_k: FakeContext()})()})()
    session = acc.BrowserSession(playwright, object(), Path("/fake/profile"), Path("/fake/cache"))
    assert set(page.handlers) == {"request", "response", "pageerror"}  # every existing capture stays registered
    handler = page.handlers["response"]
    assert type(handler).__name__ == "function"  # Playwright rejects builtin_function_or_method
    marker, request_marker = object(), type("R", (), {"url": "http://x/"})()
    handler(marker)
    assert session.responses == [marker] and session.responses[0] is marker
    page.handlers["request"](request_marker)
    page.handlers["pageerror"](RuntimeError("boom"))
    assert session.requests == ["http://x/"] and session.page_errors == 1
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        session.context.crash_handler({"status": "crashed", "errorCode": 7, "targetId": "private-target", "url": "private-url"})
    assert "renderer-crash status=crashed errorCode=7" in err.getvalue()
    assert "private-target" not in err.getvalue() and "private-url" not in err.getvalue()


def test_persist_rejects_malformed_null_wrong_shape_and_wrong_id_bodies_even_when_equal_or_different():
    ok = _resp_status({"id": 7, "points": [1]})
    raw = lambda b: {"status": 200, "headers": {}, "raw": b}  # noqa: E731
    cases = (("same malformed both sides", raw(b"{bad"), raw(b"{bad")),
             ("different malformed", raw(b"{bad"), raw(b"[oops")),
             ("null both", _resp_status(None), _resp_status(None)),
             ("list both", _resp_status([7]), _resp_status([7])),
             ("wrong id both", _resp_status({"id": 8}), _resp_status({"id": 8})),
             ("malformed before only", raw(b"{bad"), ok),
             ("valid then corrupt", ok, raw(b"{bad")))
    for label, before, after in cases:
        checks, _ = _persist(_FakeServer(before, after_restart=after))
        assert not all(checks.values()), label
    assert all(_persist(_FakeServer(ok))[0].values())  # unchanged valid detail still passes


class _Resp:
    def __init__(self, url, status):
        self.url, self.status = url, status


def test_load_map_selects_kart_surface_before_filling_search_and_still_reloads_nothing_extra():
    log = []

    class Loc:
        def __init__(self, name):
            self.name = name

        def click(self):
            log.append(("click", self.name))

        def fill(self, value):
            log.append(("fill", self.name))

    class Ctx:
        value = _Resp("http://s/api/sites?q=", 200)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class Page:
        def get_by_role(self, role, name, exact):
            assert role == "button" and exact is True
            return Loc(name)

        def get_by_label(self, name, exact):
            return Loc(name)

        def locator(self, selector):
            return Loc(selector)

        def expect_response(self, predicate):
            assert predicate(_Resp("http://s/api/sites", 200))
            return Ctx()

        def wait_for_load_state(self, _s):
            log.append(("wait", "networkidle"))

    session = acc.BrowserSession.__new__(acc.BrowserSession)
    session.page, session.server = Page(), type("S", (), {"base": "http://s"})()
    session.load_map("tok", "q")
    order = [entry for entry in log if entry[0] in ("click", "fill")]
    assert order[0] == ("click", "Kart")
    assert order.index(("click", "Kart")) < order.index(("fill", "#site-search"))
    assert order[-1] == ("click", "Last inn kart")


def test_map_load_response_predicate_accepts_sites_or_denied_session_only():
    base = "http://s"
    assert acc.session_or_sites(base, 200, base + "/api/sites?q=a")
    assert acc.session_or_sites(base, 401, base + "/api/session")
    assert acc.session_or_sites(base, 403, base + "/api/session")
    assert not acc.session_or_sites(base, 200, base + "/api/session")  # successful session precedes /api/sites
    assert not acc.session_or_sites(base, 500, base + "/api/session")
    assert not acc.session_or_sites(base, 401, base + "/api/routes")
    assert not acc.session_or_sites(base, 401, "http://evil/api/session")


def test_denied_token_list_allows_placeholder_but_rejects_a_real_site_card_without_a_name():
    placeholder = {"list_children": 1, "list_site_cards": 0, "list_has_site_name": False}
    card_no_name = {"list_children": 1, "list_site_cards": 1, "list_has_site_name": False}
    card_named = {"list_children": 1, "list_site_cards": 1, "list_has_site_name": True}
    assert acc.denied_list_clean(placeholder)
    assert not acc.denied_list_clean(card_no_name)
    assert not acc.denied_list_clean(card_named)


def test_observe_counts_real_site_item_cards_not_generic_children():
    seen = {}

    class Page:
        def eval_on_selector(self, selector, js):
            seen["js"] = js
            return {"text": "Ingen steder passer filtrene.", "children": 1, "cards": 0}

        def evaluate(self, *_a):
            return {"storage": False, "cookie": False, "url": False}

    class R(_Resp):
        headers = {}

        def body(self):
            return b"[]"

    session = acc.BrowserSession.__new__(acc.BrowserSession)
    session.page, session.requests, session.blocked, session.page_errors = Page(), [], [], 0
    record = session.observe(R("http://s/api/sites", 200), "tok")
    assert ".site-item" in seen["js"]
    assert record["list_children"] == 1 and record["list_site_cards"] == 0

def test_browser_unit_preserves_six_records_and_prior_passes_after_reload_crash():
    seen = {}

    class Playwright:
        def start(self):
            seen["debug_at_start"] = os.environ.get("DEBUG")
            return "pw"

        def stop(self):
            seen["stopped"] = True

        def __enter__(self):
            return self.start()

        def __exit__(self, *_exc):
            self.stop()

    class Page:
        crashed = False

        def goto(self, _url):
            pass

        def reload(self):
            seen["reloads"] = seen.get("reloads", 0) + 1
            self.crashed = True
            raise RuntimeError("Page.reload: Page crashed")

    class Session:
        def __init__(self, *_args):
            self.page = Page()
            self.requests = []

        def load_map(self, *_args):
            if self.page.crashed:
                raise RuntimeError("Page crashed")

        def observe(self, *_args):
            return {"status": 200, "list_children": 1000, "privacy": {"storage": False, "cookie": False, "url": False}}

        def route_view(self, *_args):
            if self.page.crashed:
                raise RuntimeError("Page crashed")
            return {"status": 200, "privacy": {"storage": False, "cookie": False, "url": False}}

        def close(self):
            seen["closed"] = True

    class Server:
        base = "http://127.0.0.1:1"

        def __init__(self, *_args):
            pass

        start = stop = lambda self: None

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = Playwright
    saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
    sys.modules["playwright"], sys.modules["playwright.sync_api"] = types.ModuleType("playwright"), module
    originals = (acc.Server, acc.request, acc.BrowserSession)
    acc.Server, acc.request, acc.BrowserSession = Server, lambda *a, **k: {"status": 200}, Session
    prior = os.environ.pop("DEBUG", None)
    os.environ["DEBUG"] = "pw:api"  # a pre-existing value must come back untouched
    err = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
            results = acc.browser_unit("large", Path(tmp))
            document = _doc("browser-large")
            data = json.loads(document)
            data["cases"] = results
            raw = json.dumps(data).encode()
            matrix.validate_result(raw, "browser-large")
            by_id = {c["id"]: c for c in results}
            assert len(by_id) == 6
            assert by_id["browser.large.cold-load"]["status"] == "PASS"
            assert by_id["browser.large.route-view-cold"]["status"] == "PASS"
            for name in ("warm-reload", "route-view-warm", "filter-sparse", "filter-dense"):
                failed = by_id[f"browser.large.{name}"]
                assert failed["status"] == "FAIL" and failed["record"] == {"error": "RuntimeError"}
            with _stub(lambda *_args: _run(raw + b"\n", returncode=1)):
                assert adm.run_unit("browser-large", Path(tmp))["status"] == "FAIL"
            real = os.statvfs(tmp)
        assert os.environ["DEBUG"] == "pw:api"
    finally:
        acc.Server, acc.request, acc.BrowserSession = originals
        os.environ.pop("DEBUG", None)
        if prior is not None:
            os.environ["DEBUG"] = prior
        for key, value in saved.items():
            sys.modules.pop(key, None)
            if value is not None:
                sys.modules[key] = value
    assert seen["debug_at_start"] == "pw:browser" and seen["stopped"] and seen["closed"]
    lines = [line for line in err.getvalue().splitlines() if line.startswith("o23-diag fs-usage")]
    assert seen["reloads"] == 2
    assert len(lines) >= 4 and "phase=before-reload-2" in lines[-1]
    assert f"total={real.f_blocks * real.f_frsize}" in lines[-1] and f"frsize={real.f_frsize}" in lines[-1]


def test_fs_usage_reports_unreadable_path_as_data_and_never_raises():
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
        acc.fs_usage("probe", Path(tmp) / "missing")
    assert "phase=probe unavailable=FileNotFoundError" in err.getvalue() and "missing" not in err.getvalue()


def test_failed_browser_cli_emits_all_cases_and_returns_nonzero():
    original = acc.browser_unit
    results = json.loads(_doc("browser-large", status="FAIL"))["cases"]
    acc.browser_unit = lambda *_args: results
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
    try:
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            status = acc.main(["--unit", "browser-large", "--scratch", tmp])
        out.flush()
        raw = out.buffer.getvalue()
        matrix.validate_result(raw, "browser-large")
        assert status == 1 and json.loads(raw)["cases"] == results
    finally:
        signal.alarm(0)
        for signum, handler in signals.items():
            signal.signal(signum, handler)
        acc.browser_unit = original
        out.close()


def test_case_does_not_convert_interrupt_or_deadline_to_a_result():
    for error in (KeyboardInterrupt(), SystemExit(143), SystemExit(142)):
        try:
            acc.case("interrupted", lambda: (_ for _ in ()).throw(error))
        except (KeyboardInterrupt, SystemExit) as caught:
            assert caught is error
        else:
            raise AssertionError("interrupt was swallowed")



if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in dir(sys.modules[__name__]) if n.startswith("test_")):
        try:
            getattr(sys.modules[__name__], name)()
            print("PASS", name)
        except Exception as error:  # noqa: BLE001
            failures += 1
            print("FAIL", name, type(error).__name__, str(error)[:120])
    sys.exit(1 if failures else 0)
