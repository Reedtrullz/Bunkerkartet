"""Original Bunkerkartet O23/U05 acceptance fixture: real app, real HTTP bytes, real Chromium.

One invocation runs one frozen unit from o23_matrix.py and prints one JSON result document.
Nothing here measures time: it records comparable semantics (status, error bodies, ordered
payload digests, wire encodings and bytes, privacy observations) so a baseline checkout and a
candidate checkout can be compared with  o23_matrix.py --compare  and fed to the existing M
protocol. It is meant to run inside o23_linux_admission.py; --self-test is pure source QA.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import o23_matrix as matrix  # noqa: E402

TOKEN = "o23-audit-only"
DEADLINE_S = 110  # below the 120 s native invocation bound; the wrapper also enforces 120 s
ROUTE = {"name": "O23 route", "start": {"lat": 63.4305, "lon": 10.3951}, "mode": "one_way", "site_ids": [1]}
SITE_LIST = "#site-list"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


DIAG_LIMIT = 64 * 1024
_diag_used = [0]


def diag(kind: str, detail: str, limit: int = 1500) -> None:
    """Bounded, redacted failure evidence on stderr (kept in the unit log before the scratch is torn down).

    Never part of a result record, so parity is unaffected; the line prefix can never be mistaken for the result document.
    """
    scratch = os.environ.get("TMPDIR")
    paths = {str(Path.home()): "<home>", str(ROOT): "<checkout>"}
    if scratch:
        paths[os.path.dirname(scratch)] = "<scratch>"
    line = f"o23-diag {kind} " + matrix.redact(detail, secrets=[TOKEN, "synthetic-ors-key"], paths=paths, limit=limit)
    if _diag_used[0] + len(line) > DIAG_LIMIT:
        return
    _diag_used[0] += len(line)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def log_tail(path: Path, size: int = 4096) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - size))
            return handle.read().decode("utf-8", "replace")
    except OSError as error:
        return f"unreadable log: {type(error).__name__}"


class Server:
    """The real app (o23_server.py) on a loopback port with its own data directory."""

    def __init__(self, data_dir: Path, sites: int, log: Path):
        self.data_dir, self.sites, self.log = data_dir, sites, log
        self.process: subprocess.Popen | None = None
        self.port = 0

    def start(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        env = {**os.environ, "O23_DATA_DIR": str(self.data_dir), "O23_SITES": str(self.sites),
               "O23_PORT": str(self.port), "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        with open(self.log, "ab") as sink:
            self.process = subprocess.Popen([sys.executable, str(ROOT / "scripts/qa/o23_server.py")], cwd=ROOT,
                                            env=env, stdout=sink, stderr=sink, start_new_session=True)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                if request(self, "GET", "/api/health", token=None)["status"] == 200:
                    return
            except OSError:
                time.sleep(0.1)  # readiness poll only; correctness is asserted on responses
        self.stop()
        diag("server-not-ready", log_tail(self.log))
        raise RuntimeError("fixture server did not become ready")

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(process.pid, sig)
                process.wait(timeout=wait)
                return
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def database_file(self) -> Path:
        files = [p for p in self.data_dir.iterdir() if p.suffix in {".db", ".sqlite", ".sqlite3"}]
        if len(files) != 1:
            raise RuntimeError("expected exactly one SQLite file")
        return files[0]


def request(server: Server, method: str, path: str, *, token: str | None = TOKEN, body=None,
            encoding: str | None = None) -> dict:
    """One real HTTP exchange; http.client never decodes, so bytes/headers are exactly the wire's."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if encoding:
        headers["Accept-Encoding"] = encoding
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=15)
    try:
        connection.request(method, path, data, headers)
        response = connection.getresponse()
        raw = response.read()
        kept = {k.lower(): v for k, v in response.getheaders() if k.lower() in matrix.HEADER_ALLOWLIST}
    finally:
        connection.close()
    return {"status": response.status, "headers": kept, "raw": raw}


def decoded(response: dict) -> bytes:
    return gzip.decompress(response["raw"]) if response["headers"].get("content-encoding") == "gzip" else response["raw"]


def parsed(response: dict):
    try:
        return json.loads(decoded(response))
    except ValueError:
        return None


def summary(response: dict, *, body: bool = True) -> dict:
    out = {"status": response["status"], "headers": response["headers"], "wire_bytes": len(response["raw"]),
           "wire_sha256": sha(response["raw"]), "decoded_sha256": sha(decoded(response))}
    if body:
        out["body_sha256"] = matrix.digest(parsed(response))
    return out


def error_body(response: dict):
    return parsed(response)


def case(case_id: str, run, emit=None) -> dict:
    """Run one case; any exception becomes a recorded FAIL, never a skipped or passed case."""
    try:
        checks, record = run()
    except Exception as error:  # noqa: BLE001 - recorded as data
        checks, record = {"completed": False}, {"error": type(error).__name__}
        (emit or diag)("case-failure", f"{case_id} {type(error).__name__}: {error}")
    checks = dict(checks) or {"completed": True}
    return {"id": case_id, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "record": record}


# ---- API unit -------------------------------------------------------------------------------

def persist_roundtrip(server, route_id) -> tuple[dict, dict]:
    """GET the same detail endpoint before and after a real restart; full parsed body and status must match."""
    path = f"/api/routes/{route_id}"

    def strict(response):
        """(valid, digest): invalid JSON keeps its own wire hash so two different malformed bodies never compare equal."""
        try:
            body = json.loads(decoded(response))
        except ValueError:
            return False, matrix.digest({"status": response["status"], "invalid_wire_sha256": sha(response["raw"])})
        ok = response["status"] == 200 and isinstance(body, dict) and body.get("id") == route_id
        return ok, matrix.digest({"status": response["status"], "body": body})

    before = request(server, "GET", path)
    server.stop()
    server.start()  # same data directory: persistence is the SQLite file, not process memory
    after = request(server, "GET", path)
    (valid_before, digest_before), (valid_after, digest_after) = strict(before), strict(after)
    return ({"create_id_is_int": isinstance(route_id, int), "status_200_before": before["status"] == 200,
             "status_200_after": after["status"] == 200, "detail_valid_before": valid_before,
             "detail_valid_after": valid_after, "identical_after_restart": digest_before == digest_after},
            summary(after))


def session_or_sites(base: str, status: int, url: str) -> bool:
    """The map load ends at /api/sites, or at a denied /api/session; a successful session 200 is only a step on the way."""
    path = urlsplit(url).path
    return url.startswith(base + "/api/sites") or (url.startswith(base + "/") and path == "/api/session" and status in (401, 403))


def denied_list_clean(record: dict) -> bool:
    """Denied token: no real site card rendered (the benign .empty-state placeholder is allowed) and no site names."""
    return record["list_site_cards"] == 0 and not record["list_has_site_name"]


def ids_of(response: dict) -> list[int]:
    body = parsed(response)
    return [item["id"] for item in body] if isinstance(body, list) else []


def api_unit(size: str, scratch: Path) -> list[dict]:
    from app.main import RouteRequest, _payload_digest  # noqa: PLC0415 - needs the app importable

    count = matrix.SIZES[size]
    server = Server(scratch / "data", count, scratch / "server.log")
    results: dict[str, dict] = {}
    state: dict = {}
    try:
        server.start()
        full = None
        for name, params in matrix.FILTERS.items():
            query = "&".join(f"{k}={v}" for k, v in params.items())
            path = "/api/sites" + (f"?{query}" if query else "")
            pair = {}
            for encoding in matrix.ENCODINGS:
                def run(encoding=encoding, path=path, name=name, pair=pair):
                    response = request(server, "GET", path, encoding=encoding)
                    pair[encoding] = response
                    ids = ids_of(response)
                    expected_all = set(ids) == set(range(1, count + 1))
                    shape = {"unfiltered": expected_all, "dense": expected_all, "kind": expected_all,
                             "sparse": 3 in ids and 0 < len(ids) < count, "nomatch": ids == []}[name]
                    checks = {"status_200": response["status"] == 200, "expected_membership": shape,
                              "requested_encoding_honoured_or_identity": response["headers"].get("content-encoding") in (None, "gzip")}
                    if "identity" in pair and encoding == "gzip":
                        checks["decoded_body_equals_identity_body"] = decoded(response) == decoded(pair["identity"])
                    record = {**summary(response, body=False), "ordered_ids": ids, "count": len(ids),
                              "request_accept_encoding": encoding, "wire_encoding": response["headers"].get("content-encoding", "identity")}
                    return checks, record
                results[f"api.{size}.list-{name}.{encoding}"] = case(f"api.{size}.list-{name}.{encoding}", run)

        def create():
            body = {**ROUTE, "request_id": "o23-route-1"}
            response = request(server, "POST", "/api/routes", body=body)
            payload = parsed(response) or {}
            state["route_id"], state["route_sha"] = payload.get("id"), matrix.digest(payload)
            return ({"status_200": response["status"] == 200, "has_id": isinstance(payload.get("id"), int)},
                    summary(response))
        results[f"api.{size}.route.create"] = case(f"api.{size}.route.create", create)

        def persist():
            return persist_roundtrip(server, state["route_id"])
        results[f"api.{size}.route.persist-restart"] = case(f"api.{size}.route.persist-restart", persist)

        def listing():
            response = request(server, "GET", "/api/routes")
            body = parsed(response)
            ids = [item.get("id") for item in body] if isinstance(body, list) else []
            return {"status_200": response["status"] == 200, "contains_route": state["route_id"] in ids}, summary(response)
        results[f"api.{size}.route.list"] = case(f"api.{size}.route.list", listing)

        for rid, token in (("route.auth-none", None), ("route.auth-wrong", "wrong-token")):
            def denied(token=token):
                response = request(server, "GET", "/api/routes", token=token)
                return ({"denied": response["status"] in (401, 403),
                         "no_private_name": b"O23 route" not in response["raw"]}, summary(response))
            results[f"api.{size}.{rid}"] = case(f"api.{size}.{rid}", denied)

        def missing():
            response = request(server, "GET", "/api/routes/999999")
            return {"status_404": response["status"] == 404}, {**summary(response), "error": error_body(response)}
        results[f"api.{size}.route.missing-404"] = case(f"api.{size}.route.missing-404", missing)

        def invalid_payload():
            response = request(server, "POST", "/api/routes", body={"site_ids": []})
            return {"status_422": response["status"] == 422}, {**summary(response, body=False), "error": error_body(response)}
        results[f"api.{size}.route.invalid-payload"] = case(f"api.{size}.route.invalid-payload", invalid_payload)

        def invalid_site():
            response = request(server, "POST", "/api/routes", body={**ROUTE, "site_ids": [999999], "request_id": "o23-bad-site"})
            return ({"rejected_4xx": 400 <= response["status"] < 500},
                    {**summary(response, body=False), "error": error_body(response)})
        results[f"api.{size}.route.invalid-site"] = case(f"api.{size}.route.invalid-site", invalid_site)

        def lease(rid, expires_at, expect_status):
            def run():
                body = {**ROUTE, "request_id": rid}
                digest = _payload_digest(RouteRequest.model_validate(body).model_dump(exclude={"request_id"}))
                with sqlite3.connect(server.database_file(), timeout=10) as connection:
                    connection.execute("INSERT INTO route_requests(request_id,payload_hash,expires_at) VALUES(?,?,?)",
                                       (rid, digest, expires_at))
                response = request(server, "POST", "/api/routes", body=body)
                with sqlite3.connect(server.database_file(), timeout=10) as connection:
                    row = connection.execute("SELECT route_id FROM route_requests WHERE request_id=?", (rid,)).fetchone()
                    connection.execute("DELETE FROM route_requests WHERE request_id=? AND route_id IS NULL", (rid,))
                checks = {"status": response["status"] == expect_status}
                record = {**summary(response, body=False), "lease_row_route_bound": bool(row and row[0] is not None)}
                if expect_status == 409:
                    checks["retry_after_1"] = response["headers"].get("retry-after") == "1"
                    record["error"] = error_body(response)
                else:
                    checks["expired_lease_replaced_by_route"] = record["lease_row_route_bound"]
                return checks, record
            return run
        results[f"api.{size}.route.lease-expired"] = case(f"api.{size}.route.lease-expired", lease("o23-lease-expired", 1.0, 200))
        results[f"api.{size}.route.lease-pending"] = case(f"api.{size}.route.lease-pending", lease("o23-lease-pending", 4e9, 409))

        def erased():
            ask = {"route_ids": [state["route_id"]], "reason": "o23 acceptance erasure"}
            preview = request(server, "POST", "/api/admin/retention/routes/preview", body=ask)
            token = (parsed(preview) or {}).get("preview_hash")
            commit = request(server, "POST", "/api/admin/retention/routes/commit", body={**ask, "preview_hash": token})
            after = request(server, "GET", f"/api/routes/{state['route_id']}")
            return ({"preview_200": preview["status"] == 200, "commit_200": commit["status"] == 200,
                     "erased_410": after["status"] == 410},
                    {"preview": summary(preview, body=False), "commit": summary(commit, body=False),
                     "after": {**summary(after, body=False), "error": error_body(after)}})
        results[f"api.{size}.route.erased-410"] = case(f"api.{size}.route.erased-410", erased)
    finally:
        server.stop()
    return [results.get(c["id"]) or case(c["id"], lambda: ({"completed": False}, {"error": "NotRun"}))
            for c in matrix.cases() if c["unit"] == f"api-{size}"]


# ---- Browser unit ---------------------------------------------------------------------------

PRIVACY_JS = r"""(token) => {
  const dump = [];
  for (const store of ['localStorage', 'sessionStorage']) {
    try { const s = window[store]; for (let i = 0; i < s.length; i++) dump.push(s.key(i) + '=' + s.getItem(s.key(i))); } catch (e) {}
  }
  return {storage: dump.join('\n').includes(token), cookie: document.cookie.includes(token), url: location.href.includes(token)};
}"""
DENY_STORAGE_JS = """
for (const name of ['localStorage', 'sessionStorage'])
  Object.defineProperty(window, name, {get() { throw new DOMException('denied', 'SecurityError'); }});
if (window.indexedDB) indexedDB.open = () => { throw new DOMException('denied', 'SecurityError'); };
"""


class BrowserSession:
    def __init__(self, playwright, server: Server, profile: Path, cache: Path, init_script: str | None = None):
        self.server, self.requests, self.blocked, self.responses, self.page_errors = server, [], [], [], 0
        self.context = playwright.chromium.launch_persistent_context(
            str(profile), headless=True, viewport={"width": 1280, "height": 720},
            args=[f"--disk-cache-dir={cache}"], accept_downloads=False)
        self.context.set_default_timeout(15000)
        if init_script:
            self.context.add_init_script(init_script)
        self.context.route("**/*", self._gate)
        self.page = self.context.new_page()
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on("response", lambda r: self.responses.append(r))  # Playwright needs a Python function, not a builtin
        self.page.on("pageerror", lambda _e: setattr(self, "page_errors", self.page_errors + 1))

    def _gate(self, route):
        url = route.request.url
        if url.startswith(self.server.base + "/") or url.startswith("data:"):
            route.continue_()
        else:
            self.blocked.append(urlsplit(url)._replace(query="").geturl())  # egress attempt, aborted
            route.abort()

    def load_map(self, token: str, query: str | None = None):
        page = self.page
        page.get_by_role("button", name="Kart", exact=True).click()  # a reload can keep surface=route; #site-search lives on the map surface
        page.get_by_label("Administratortoken", exact=True).fill(token)
        page.locator("#site-search").fill(query or "")
        with page.expect_response(lambda r: session_or_sites(self.server.base, r.status, r.url)) as info:
            page.get_by_role("button", name="Last inn kart", exact=True).click()
        response = info.value
        page.wait_for_load_state("networkidle")
        return response

    def observe(self, response, token: str) -> dict:
        page = self.page
        listing = page.eval_on_selector(
            SITE_LIST, "e => ({text: e.innerText, children: e.children.length, cards: e.querySelectorAll('.site-item').length})")
        privacy = page.evaluate(PRIVACY_JS, token)
        query = parse_qs(urlsplit(response.url).query)
        return {"status": response.status, "query": query, "content_encoding": response.headers.get("content-encoding", "identity"),
                "content_length": response.headers.get("content-length"),
                "decoded_body_sha256": sha(response.body()), "list_children": listing["children"],
                "list_site_cards": listing["cards"],
                "list_text_sha256": sha(listing["text"].encode()), "list_has_site_name": "Synthetic site" in listing["text"],
                "privacy": privacy, "token_in_any_request_url": any(token in u for u in self.requests),
                "egress_attempts_blocked": sorted(set(self.blocked)), "page_errors": self.page_errors}

    def route_view(self, token: str) -> dict:
        page = self.page
        page.get_by_role("button", name="Tur", exact=True).click()
        with page.expect_response(lambda r: r.url.startswith(self.server.base + "/api/routes")) as info:
            page.get_by_role("button", name="Last inn rute", exact=True).click()
        page.wait_for_load_state("networkidle")
        view = page.eval_on_selector("#route-history-list", "e => ({text: e.innerText, children: e.children.length})")
        return {"status": info.value.status, "history_children": view["children"],
                "history_text_sha256": sha(view["text"].encode()),
                "privacy": page.evaluate(PRIVACY_JS, token), "page_errors": self.page_errors}

    def close(self) -> None:
        self.context.close()


def privacy_clean(record: dict) -> bool:
    p = record["privacy"]
    return not (p["storage"] or p["cookie"] or p["url"] or record.get("token_in_any_request_url"))


def browser_unit(size: str, scratch: Path) -> list[dict]:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    count = matrix.SIZES[size]
    server = Server(scratch / "data", count, scratch / "server.log")
    got: dict[str, dict] = {}
    try:
        server.start()
        seeded = request(server, "POST", "/api/routes", body={**ROUTE, "request_id": "o23-browser-route"})
        with sync_playwright() as pw:
            session = BrowserSession(pw, server, scratch / "profile", scratch / "cache")
            try:
                session.page.goto(server.base)
                def listed(name, query=None, expect_q=None, fewer=False):
                    def run():
                        record = session.observe(session.load_map(TOKEN, query), TOKEN)
                        checks = {"status_200": record["status"] == 200, "list_nonempty": record["list_children"] > 0,
                                  "privacy_clean": privacy_clean(record)}
                        if expect_q is not None:
                            checks["filter_sent_to_server"] = record["query"].get("q") == [expect_q]
                        if fewer:
                            checks["sparse_list_smaller_than_catalogue"] = record["list_children"] < count
                        return checks, record
                    return case(f"browser.{size}.{name}", run)
                def routed(name):
                    def run():
                        record = session.route_view(TOKEN)
                        return ({"status_200": record["status"] == 200, "route_seeded": seeded["status"] == 200,
                                 "privacy_clean": privacy_clean({**record, "token_in_any_request_url": any(TOKEN in u for u in session.requests)})}, record)
                    return case(f"browser.{size}.{name}", run)
                got["cold-load"] = listed("cold-load")
                got["route-view-cold"] = routed("route-view-cold")
                session.page.reload()  # same profile and context: the warm revisit
                got["warm-reload"] = listed("warm-reload")
                got["route-view-warm"] = routed("route-view-warm")
                session.page.reload()
                got["filter-sparse"] = listed("filter-sparse", "0003", "0003", fewer=True)
                got["filter-dense"] = listed("filter-dense", "synthetic", "synthetic")
            finally:
                session.close()
    finally:
        server.stop()
    wanted = [c for c in matrix.cases() if c["unit"] == f"browser-{size}"]
    return [got.get(c["id"].split(".", 2)[2]) or case(c["id"], lambda: ({"completed": False}, {"error": "NotRun"})) for c in wanted]


# ---- Adverse unit ---------------------------------------------------------------------------

def adverse_unit(scratch: Path) -> list[dict]:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    got: dict[str, dict] = {}

    def browse(server, name, tag, flow, init_script=None):
        def run():
            with sync_playwright() as pw:
                session = BrowserSession(pw, server, scratch / f"profile-{tag}", scratch / f"cache-{tag}", init_script)
                try:
                    session.page.goto(server.base)
                    return flow(session)
                finally:
                    session.close()
        got[name] = case(f"adverse.small.{name}", run)

    empty = Server(scratch / "data-empty", 0, scratch / "server.log")
    try:
        empty.start()
        def empty_api():
            response = request(empty, "GET", "/api/sites")
            return {"status_200": response["status"] == 200, "empty_list": parsed(response) == []}, summary(response, body=False)
        got["empty-catalogue-api"] = case("adverse.small.empty-catalogue-api", empty_api)
        def empty_ui(session):
            record = session.observe(session.load_map(TOKEN), TOKEN)
            return ({"status_200": record["status"] == 200, "no_site_names": not record["list_has_site_name"],
                     "privacy_clean": privacy_clean(record)}, record)
        browse(empty, "empty-catalogue-browser", "empty", empty_ui)
    finally:
        empty.stop()

    main = Server(scratch / "data-main", matrix.SIZES["small"], scratch / "server.log")
    try:
        main.start()
        def bad_token(session):
            record = session.observe(session.load_map("wrong-token"), "wrong-token")
            return ({"denied": record["status"] in (401, 403), "no_site_cards": record["list_site_cards"] == 0,
                     "no_site_names": not record["list_has_site_name"], "privacy_clean": privacy_clean(record),
                     "list_clean": denied_list_clean(record)}, record)
        browse(main, "invalid-token-browser", "token", bad_token)
        def denied_storage(session):
            record = session.observe(session.load_map(TOKEN), TOKEN)
            return ({"request_completed": record["status"] == 200, "privacy_clean": privacy_clean(record)}, record)
        browse(main, "denied-storage-browser", "denied", denied_storage, DENY_STORAGE_JS)
        def offline(session):
            online = session.observe(session.load_map(TOKEN), TOKEN)
            session.context.set_offline(True)
            try:
                session.page.goto(main.base)
                navigated, failure = True, None
            except Exception as error:  # noqa: BLE001 - the browser's offline error class is the observation
                navigated, failure = False, re.search(r"net::ERR_[A-Z_]+", str(error))
                failure = failure.group(0) if failure else type(error).__name__
            session.context.set_offline(False)
            session.page.goto(main.base)
            after = session.page.evaluate(PRIVACY_JS, TOKEN)
            token_field = session.page.get_by_label("Administratortoken", exact=True).input_value()
            return ({"online_load_ok": online["status"] == 200, "privacy_clean": privacy_clean(online),
                     "token_not_persisted_after_revisit": token_field == "" and not (after["storage"] or after["cookie"] or after["url"])},
                    {"offline_navigation_succeeded": navigated, "offline_failure": failure, "online": online,
                     "token_field_empty_after_revisit": token_field == ""})
        browse(main, "offline-revisit-browser", "offline", offline)
        def unavailable():
            db = main.database_file()
            moved = db.with_suffix(db.suffix + ".o23moved")
            os.rename(db, moved)
            db.mkdir()  # sqlite cannot open a directory: a real, reversible storage failure
            try:
                response = request(main, "GET", "/api/sites")
            finally:
                db.rmdir()
                os.rename(moved, db)
            body = parsed(response) or {}
            code = (body.get("detail") or {}).get("code") if isinstance(body, dict) and isinstance(body.get("detail"), dict) else None
            recovered = request(main, "GET", "/api/sites")
            return ({"status_503": response["status"] == 503, "code_DATABASE_UNAVAILABLE": code == "DATABASE_UNAVAILABLE",
                     "recovers_after_restore": recovered["status"] == 200},
                    {**summary(response, body=False), "error": error_body(response), "recovered_status": recovered["status"]})
        got["unavailable-db-api"] = case("adverse.small.unavailable-db-api", unavailable)
    finally:
        main.stop()
    return [got.get(c["id"].split(".", 2)[2]) or case(c["id"], lambda: ({"completed": False}, {"error": "NotRun"}))
            for c in matrix.cases() if c["unit"] == "adverse-small"]


# ---- CLI -----------------------------------------------------------------------------------

def self_test() -> None:
    matrix.self_test()
    body = {"created_at": "2026-01-01T12:00:00+00:00", "expires_at": 1.0, "v": [2, 1]}
    shown = summary({"status": 200, "headers": {}, "raw": json.dumps(body).encode()})
    assert shown["body_sha256"] == matrix.digest(body) and "scrubbed_body_sha256" not in shown  # no field is dropped
    assert error_body({"status": 200, "headers": {}, "raw": b'{"d": "<time>x</time>"}'}) == {"d": "<time>x</time>"}
    seen: list[str] = []
    failed = case("x", lambda: (_ for _ in ()).throw(KeyError("secret path /tmp/x")), emit=lambda k, d: seen.append(d))
    assert failed["status"] == "FAIL" and failed["record"] == {"error": "KeyError"}  # record stays class-only
    assert seen and "KeyError" in seen[0]  # the cause goes to bounded redacted diagnostics instead
    assert case("y", lambda: ({"a": True, "b": False}, {}))["status"] == "FAIL"
    assert case("z", lambda: ({"a": True}, {}))["status"] == "PASS"
    assert gzip.decompress(gzip.compress(b"x")) == decoded({"raw": gzip.compress(b"x"), "headers": {"content-encoding": "gzip"}})
    assert decoded({"raw": b"x", "headers": {}}) == b"x"  # an identity response is never modelled as gzip
    print("o23_acceptance self-test ok (pure source check; no app, browser or native result)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true")
    group.add_argument("--unit", choices=matrix.UNITS)
    parser.add_argument("--scratch", type=Path, help="owned, empty runtime directory (required with --unit)")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.scratch is None or not args.scratch.is_dir() or any(args.scratch.iterdir()):
        parser.error("--scratch must be an existing empty directory")

    def interrupted(signum, _frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # repeated interrupts cannot abort cleanup
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    for name in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(name, interrupted)
    signal.alarm(DEADLINE_S)
    kind, _, size = args.unit.partition("-")
    results = {"api": lambda: api_unit(size, args.scratch), "browser": lambda: browser_unit(size, args.scratch),
               "adverse": lambda: adverse_unit(args.scratch)}[kind]()
    signal.alarm(0)
    if any(c["status"] != "PASS" for c in results):
        for log in sorted(args.scratch.rglob("*.log"))[:4]:  # primary cause before the scratch is deleted
            diag(f"server-log {log.name}", log_tail(log))
    raw = json.dumps({"schema": matrix.SCHEMA, "unit": args.unit, "python": sys.version.split()[0], "cases": results},
                     sort_keys=True).encode()
    try:
        matrix.validate_result(raw, args.unit)
    except ValueError as error:
        print(f"invalid result document: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(raw + b"\n")
    return 0 if all(c["status"] == "PASS" for c in results) else 1


if __name__ == "__main__":
    sys.exit(main())
