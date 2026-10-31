"""Frozen Linux native admission for the O23 acceptance fixture (hosted ubuntu CI path only).

Containment per invocation, all from supported util-linux/kernel features; each element is proven by
a positive or negative control (--controls) before any unit is admitted:

* network: private network namespace (only loopback) - external connects must fail;
* private reads: HOME is replaced by an empty read-only tmpfs and the checkout .git is masked, so
  credentials/.git of the real HOME and checkout are unreadable; only the checkout (read-only, minus
  .git), the interpreter tree and the preinstalled Playwright browsers (read-only bind) are visible;
* writes: every host mount is remounted read-only; /tmp, /var/tmp and /dev/shm are bind-mounted from
  the one scratch tmpfs, so nothing can be written outside it;
* runtime disk: one 48 MiB tmpfs holds HOME/TMPDIR/browser profile+cache/server data/shm. The other
  16 MiB of the approved combined 64 MiB is reserved for parent artifacts/caches (ARTIFACT_BYTES);
  a full 64 MiB native reservation is not an admitted case;
* privileges: setpriv drops to the caller's uid with no_new_privs and empty inheritable/bounding sets;
* time/processes: pid namespace with --kill-child, timeout --signal=KILL 120 s, wrapper deadline;
* output: combined stdout+stderr read through a 1 MiB cap; failure diagnostics (server log tails,
  exception text) are emitted redacted and bounded on that stream before teardown.

Needs sudo -n; if any preflight item is missing the run is BLOCKED, never downgraded. The probe adds no
explicit --no-sandbox, but omitting that flag and Playwright defaults are not evidence of Chromium's own
sandbox state; the actual launch configuration is PENDING native inspection. The outer privacy, egress,
write and capability guards are the mandatory controls. If the runner forbids the browser under these
guards the positive app/browser control fails and the exact cause is retained in controls.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import o23_matrix as matrix  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 * 1024
SCRATCH_BYTES = 48 * MIB
ARTIFACT_BYTES = 16 * MIB  # parent caches + artifacts headroom inside the approved combined 64 MiB
TIMEOUT_S = 120
STREAM_CAP = MIB
# Fixed script; every variable arrives as a positional argument, never by interpolation.
SCRIPT = """set -eu
scratch=$1; uid=$2; gid=$3; root=$4; home=$5; pw=$6; limit=$7; size=$8; shift 8
mount --make-rprivate /
mkdir -p "$scratch"
mount -t tmpfs -o "size=$size,mode=0700,uid=$uid,gid=$gid,nosuid,nodev" tmpfs "$scratch"
install -d -m 0700 "$scratch/.stage" "$scratch/.stage/root" "$scratch/.stage/pw"
mount --bind "$root" "$scratch/.stage/root"
mount --bind "$pw" "$scratch/.stage/pw"
mount -t tmpfs -o size=1m,mode=0755,nosuid,nodev tmpfs "$home"
mkdir -p "$root" "$pw"
mount --bind "$scratch/.stage/root" "$root"
mount --bind "$scratch/.stage/pw" "$pw"
umount -l "$scratch/.stage/root" "$scratch/.stage/pw"
if [ -d "$root/.git" ]; then mount -t tmpfs -o size=4k,mode=0555,ro tmpfs "$root/.git"
elif [ -e "$root/.git" ]; then mount --bind /dev/null "$root/.git"; fi
while read -r mp; do
  case "$mp" in "$scratch"|"$scratch"/*|/proc|/proc/*|/sys|/sys/*|/dev/pts) continue;; esac
  mount -o remount,bind,ro "$mp" 2>/dev/null || { printf 'o23-diag remount-failed %.200s\\n' "$mp" >&2; exit 97; }
done <<EOF
$(awk '{print $5}' /proc/self/mountinfo)
EOF
install -d -o "$uid" -g "$gid" -m 0700 "$scratch/home" "$scratch/tmp" "$scratch/shm" "$scratch/cache" "$scratch/config" "$scratch/run" "$scratch/work"
mount --bind "$scratch/tmp" /tmp
if [ -d /var/tmp ]; then mount --bind "$scratch/tmp" /var/tmp; fi
if [ -d /dev/shm ]; then mount --bind "$scratch/shm" /dev/shm; fi
ip link set lo up
exec timeout --signal=KILL "$limit" setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs --inh-caps=-all --bounding-set=-all "$@"
"""
EGRESS = r"""import socket, json
out = {}
for host, port in (("192.0.2.1", 80), ("1.1.1.1", 443)):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); out[host] = "CONNECTED"
    except OSError as e:
        out[host] = type(e).__name__
    finally:
        s.close()
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
socket.create_connection(srv.getsockname(), timeout=3).close(); out["loopback"] = "OK"
print(json.dumps(out))"""
WRITES = r"""import json, os, sys
out = {}
for label, base in (("checkout", sys.argv[1]), ("home", sys.argv[2]), ("scratch", os.environ["TMPDIR"])):
    path = os.path.join(base, "o23-write-probe")
    try:
        open(path, "w").close(); os.unlink(path); out[label] = "WRITABLE"
    except OSError as e:
        out[label] = e.errno
print(json.dumps(out))"""
# Private canary/.git reads and outside writes. Prints errno names only, never file contents.
PRIVATE = r"""import errno, json, os, sys
canary, root, host_dir, marker, host_home = sys.argv[1:6]
def code(error): return errno.errorcode.get(error.errno, str(error.errno))
def read(path):
    try:
        with open(path, "rb") as handle: handle.read(1)
        return "READ"
    except OSError as e: return code(e)
def write(path):
    try:
        open(path, "w").close(); os.unlink(path); return "WRITABLE"
    except OSError as e: return e.errno
out = {"home_canary": read(canary), "git_head": read(os.path.join(root, ".git", "HEAD")),
       "outside": {"checkout": write(os.path.join(root, "o23-write-probe")),
                   "home": write(os.path.join(host_home, "o23-write-probe")),
                   "host_dir": "PRESENT" if os.path.exists(host_dir) else "ABSENT"}}
for base in ("/tmp", "/var/tmp", "/dev/shm"):
    try:
        with open(os.path.join(base, marker), "w") as handle: handle.write("x")
    except OSError: pass
print(json.dumps(out))"""
PRIVS = r"""import errno, json, os, subprocess
status = dict(line.split(":", 1) for line in open("/proc/self/status").read().splitlines() if ":" in line)
def attempt(argv):
    try: return "OK" if subprocess.run(argv, capture_output=True, timeout=10).returncode == 0 else "DENIED"
    except (OSError, subprocess.SubprocessError): return "DENIED"
try: os.setuid(0); setuid0 = "OK"
except OSError as e: setuid0 = errno.errorcode.get(e.errno, str(e.errno))
print(json.dumps({"nnp": int(status["NoNewPrivs"]), "caps": {k: status[k].strip() for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")},
                  "sudo": attempt(["sudo", "-n", "true"]), "setuid0": setuid0, "mount": attempt(["mount", "-t", "tmpfs", "x", "/mnt"])}))"""
# Positive control: the authored fixture app and Chromium inside the same containment (Chromium sandbox state unproven).
PROBE_APP = r"""import json, os, sys
from pathlib import Path
root, work = sys.argv[1], Path(sys.argv[2])
sys.path[:0] = [root, os.path.join(root, "scripts/qa")]
import o23_acceptance as acc
from playwright.sync_api import sync_playwright
out = {}
server = acc.Server(work / "data", 3, work / "server.log")
try:
    server.start()
    out["health"] = acc.request(server, "GET", "/api/health", token=None)["status"]
    out["sites"] = acc.ids_of(acc.request(server, "GET", "/api/sites"))
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(work / "profile"), headless=True, args=[f"--disk-cache-dir={work / 'cache'}"])
        try:
            page = context.new_page()
            out["page"] = page.goto(server.base + "/").status
            out["search_box"] = page.locator("#site-search").count()
        finally:
            context.close()
    out["profile_in_scratch"] = (work / "profile").is_dir() and str(work).startswith(os.path.dirname(os.environ["TMPDIR"]))
    out["server_log_in_scratch"] = (work / "server.log").is_file()
finally:
    server.stop()
print(json.dumps(out))"""
OVERLIMIT = r"""import json, os
chunk = b"x" * (1 << 20); n = 0; paths = [os.path.join(d, "fill") for d in (os.environ["TMPDIR"], "/tmp", "/dev/shm")]
try:
    while n < 200:
        with open(paths[n % 3], "ab") as f:
            f.write(chunk); f.flush(); os.fsync(f.fileno())
        n += 1
    print(json.dumps({"errno": None, "mib": n}))
except OSError as e:
    print(json.dumps({"errno": e.errno, "mib": n}))
finally:
    for path in paths:
        try: os.unlink(path)
        except OSError: pass"""
SURVIVOR = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)', sys.argv[1]], start_new_session=True)"
SLEEP = "import time; time.sleep(60)"
FLOOD = "import sys\nwhile True: sys.stdout.write('x' * 65536)"
FLOOD_EXITS = {-15, -9, -13, 143, 137, 141, 120}

REQUIRED_CONTROLS = (
    "positive: fixture self-test runs inside containment",
    "positive: authored fixture app and Chromium run inside containment",
    "negative: external egress denied, loopback works",
    "negative: checkout/home read-only, scratch writable",
    "negative: private HOME canary and checkout .git unreadable, outside writes denied",
    "negative: no new privileges and no capabilities",
    "negative: runtime write beyond the scratch bound stopped by ENOSPC",
    "negative: detached child does not survive",
    "negative: deadline kills long command",
    "negative: >1 MiB output terminated",
)


def uid_gid() -> tuple[int, int]:
    return os.getuid(), os.getgid()


def browsers_path() -> Path:
    return Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache/ms-playwright")


def outer_argv(inner: list[str], nonce: str, *, limit: int = TIMEOUT_S, uid: int | None = None, gid: int | None = None) -> list[str]:
    default_uid, default_gid = uid_gid()
    uid = default_uid if uid is None else uid
    gid = default_gid if gid is None else gid
    scratch = f"/run/o23-{nonce}"
    home = str(Path.home())
    env = [f"PATH={Path(sys.executable).parent}:/usr/bin:/bin", f"HOME={scratch}/home", f"TMPDIR={scratch}/tmp",
           f"XDG_CACHE_HOME={scratch}/cache", f"XDG_CONFIG_HOME={scratch}/config", f"XDG_RUNTIME_DIR={scratch}/run",
           "LANG=C", "PYTHONDONTWRITEBYTECODE=1", "PYTHONHASHSEED=0", f"PYTHONPATH={ROOT}",
           f"PLAYWRIGHT_BROWSERS_PATH={browsers_path()}"]
    return ["sudo", "-n", "unshare", "--net", "--mount", "--pid", "--fork", "--kill-child", "--mount-proc",
            "sh", "-c", SCRIPT, "o23", scratch, str(uid), str(gid), str(ROOT), home, str(browsers_path()), str(limit),
            str(SCRATCH_BYTES), "env", "-i", *env, *inner]


def unit_inner(unit: str, nonce: str) -> list[str]:
    return [sys.executable, str(ROOT / "scripts/qa/o23_acceptance.py"), "--unit", unit, "--scratch", f"/run/o23-{nonce}/work"]


def survivors(nonce: str) -> int:
    marker = f"/run/o23-{nonce}".encode()
    count = 0
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) != os.getpid():
            try:
                if marker in (entry / "cmdline").read_bytes():
                    count += 1
            except OSError:
                pass
    return count


def run_contained(inner: list[str], nonce: str, *, limit: int = TIMEOUT_S, cap: int = STREAM_CAP) -> dict:
    started = time.monotonic()
    process = subprocess.Popen(outer_argv(inner, nonce, limit=limit), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer, overflow, late = bytearray(), False, False
    while True:
        if time.monotonic() - started > limit + 5:
            late = True
            break
        if not selector.select(1):
            continue
        data = os.read(process.stdout.fileno(), 65536)
        if not data:
            break
        buffer += data
        if len(buffer) > cap:
            overflow = True
            break
    if overflow or late:
        process.stdout.close()  # writers now get SIGPIPE; sudo relays the termination
        process.terminate()
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait(timeout=10)
    selector.close()
    leftover = survivors(nonce)
    subprocess.run(["sudo", "-n", "rmdir", f"/run/o23-{nonce}"], capture_output=True, check=False)
    return {"returncode": code, "output": bytes(buffer[:cap]), "overflow": overflow, "wrapper_deadline": late,
            "elapsed_s": round(time.monotonic() - started, 2), "survivors": leftover,
            "scratch_removed": not Path(f"/run/o23-{nonce}").exists()}


def last_json(output: bytes):
    for line in reversed(output.splitlines()):
        if line.startswith(b"{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def clean(run: dict, *, exit_ok: bool) -> bool:
    """Process state every run must end in: allowed exit, no overflow/deadline, no child, scratch gone."""
    return exit_ok and not run["overflow"] and not run["wrapper_deadline"] and run["survivors"] == 0 and bool(run["scratch_removed"])


def preflight() -> dict:
    help_text, setpriv_help = "", ""
    if shutil.which("unshare"):
        help_text = subprocess.run(["unshare", "--help"], capture_output=True, text=True, check=False).stdout
    if shutil.which("setpriv", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin"):
        setpriv_help = subprocess.run(["setpriv", "--help"], capture_output=True, text=True, check=False).stdout
    chromium = list(browsers_path().glob("chromium*")) if browsers_path().is_dir() else []
    sudo = shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True, check=False).returncode == 0
    home, interpreter = Path.home().resolve(), Path(sys.executable).resolve()
    visible = not interpreter.is_relative_to(home) or interpreter.is_relative_to(ROOT.resolve())
    checks = {"linux": platform.system() == "Linux", "python_3_12": sys.version_info[:2] == (3, 12),
              "not_root": os.getuid() != 0, "sudo_noninteractive": bool(sudo),
              "tools": all(shutil.which(t, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin") for t in ("unshare", "setpriv", "timeout", "ip", "mount", "awk")),
              "unshare_flags": all(flag in help_text for flag in ("--kill-child", "--mount-proc", "--net")),
              "setpriv_flags": all(flag in setpriv_help for flag in ("--no-new-privs", "--inh-caps", "--bounding-set")),
              "preinstalled_chromium": bool(chromium), "checkout_outside_run": not str(ROOT).startswith("/run/"),
              "interpreter_visible_after_home_mask": visible}
    return {"checks": checks, "ready": all(checks.values())}


def judge_private(seen: dict, host: dict) -> bool:
    """Private reads denied and no write reached the host (host = state read back after the run)."""
    outside = seen.get("outside") or {}
    denied = {"ENOENT", "EACCES", "EPERM", "ENOTDIR"}
    return (seen.get("home_canary") in denied and seen.get("git_head") in denied and outside.get("checkout") == 30
            and outside.get("home") == 30 and outside.get("host_dir") == "ABSENT"
            and host.get("canary_intact") is True and host.get("outside_entries") == [])


def judge_privs(seen: dict) -> bool:
    caps = seen.get("caps") or {}
    try:
        empty = set(caps) == {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"} and all(int(v, 16) == 0 for v in caps.values())
    except (TypeError, ValueError):
        return False
    return (seen.get("nnp") == 1 and empty and seen.get("sudo") == "DENIED" and seen.get("setuid0") == "EPERM"
            and seen.get("mount") == "DENIED")


def judge_app(seen: dict) -> bool:
    return (seen.get("health") == 200 and seen.get("sites") == [1, 2, 3] and seen.get("page") == 200
            and seen.get("search_box") == 1 and seen.get("profile_in_scratch") is True and seen.get("server_log_in_scratch") is True)


def controls_complete(report: list[dict]) -> bool:
    return [c.get("control") for c in report] == list(REQUIRED_CONTROLS) and all(c.get("pass") is True for c in report)


def controls(home: Path | None = None) -> list[dict]:
    py, results = sys.executable, []
    home = Path(home) if home else Path.home()
    real_home = str(Path.home())

    def record(name, expected, run, seen, ok):
        passed = bool(ok(run, seen))
        observed = {"run": {k: run[k] for k in ("returncode", "overflow", "wrapper_deadline", "elapsed_s", "survivors", "scratch_removed")},
                    "seen": seen}
        if not passed:  # keep the bounded, redacted primary cause (mount/browser/app errors) before it is lost
            observed["output_tail"] = matrix.redact(run["output"][-3000:].decode("utf-8", "replace"),
                                                    paths={real_home: "<home>", str(ROOT): "<checkout>"}, limit=1500)
        results.append({"control": name, "expected": expected, "observed": observed, "pass": passed})

    nonce = f"c{os.getpid()}"
    canary_dir = Path(tempfile.mkdtemp(prefix=".o23-canary-", dir=home))
    outside_dir = Path(tempfile.mkdtemp(prefix="o23-out-", dir="/var/tmp"))
    marker = f"o23-probe-{nonce}"
    try:
        canary = canary_dir / "private"
        canary.write_text(f"o23-canary-{nonce}")  # credential-free; real secrets are never read
        canary.chmod(0o600)
        names = iter(REQUIRED_CONTROLS)
        r = run_contained([py, str(ROOT / "scripts/qa/o23_acceptance.py"), "--self-test"], nonce + "a")
        record(next(names), "exit 0, clean teardown", r, {}, lambda r, s: clean(r, exit_ok=r["returncode"] == 0))
        r = run_contained([py, "-c", PROBE_APP, str(ROOT), f"/run/o23-{nonce}p/work"], nonce + "p")
        seen = last_json(r["output"]) or {}
        record(next(names), "app health, sites, Chromium page and scratch-only profile/log", r, seen,
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and judge_app(s))
        r = run_contained([py, "-c", EGRESS], nonce + "b")
        seen = last_json(r["output"]) or {}
        record(next(names), "exit 0; external != CONNECTED; loopback OK", r, seen,
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and s.get("192.0.2.1") not in (None, "CONNECTED")
               and s.get("1.1.1.1") not in (None, "CONNECTED") and s.get("loopback") == "OK")
        r = run_contained([py, "-c", WRITES, str(ROOT), real_home], nonce + "c")
        seen = last_json(r["output"]) or {}
        record(next(names), "EROFS(30) on checkout+home, scratch WRITABLE", r, seen,
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and s.get("checkout") == 30 and s.get("home") == 30 and s.get("scratch") == "WRITABLE")
        r = run_contained([py, "-c", PRIVATE, str(canary), str(ROOT), str(outside_dir), marker, real_home], nonce + "q")
        seen = last_json(r["output"]) or {}
        leaked = [str(Path(base) / marker) for base in ("/tmp", "/var/tmp", "/dev/shm") if (Path(base) / marker).exists()]
        host = {"canary_intact": canary.exists() and canary.read_text() == f"o23-canary-{nonce}",
                "outside_entries": leaked + [str(p) for p in outside_dir.iterdir()]}
        record(next(names), "canary/.git denied, EROFS outside, host untouched", r, {**seen, "host": host},
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and judge_private(s, host))
        r = run_contained([py, "-c", PRIVS], nonce + "r")
        seen = last_json(r["output"]) or {}
        record(next(names), "NoNewPrivs 1, all caps 0, sudo/setuid(0)/mount denied", r, seen,
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and judge_privs(s))
        r = run_contained([py, "-c", OVERLIMIT], nonce + "d")
        seen = last_json(r["output"]) or {}
        record(next(names), f"errno 28 at <= {SCRATCH_BYTES // MIB} MiB across TMPDIR, /tmp and /dev/shm", r, seen,
               lambda r, s: clean(r, exit_ok=r["returncode"] == 0) and s.get("errno") == 28 and 0 < s.get("mib", 0) <= SCRATCH_BYTES // MIB)
        r = run_contained([py, "-c", SURVIVOR, f"/run/o23-{nonce}e/work"], nonce + "e")
        record(next(names), "exit 0, survivors 0, scratch removed", r, {}, lambda r, s: clean(r, exit_ok=r["returncode"] == 0))
        r = run_contained([py, "-c", SLEEP], nonce + "f", limit=3)
        record(next(names), "returncode 124 or 137 after >= 3 s and < 15 s, survivors 0, scratch removed", r, {},
               lambda r, s: clean(r, exit_ok=r["returncode"] in (124, 137)) and 3 <= r["elapsed_s"] < 15)
        r = run_contained([py, "-c", FLOOD], nonce + "g", cap=STREAM_CAP)
        record(next(names), "overflow, terminated exit, no survivors within 20 s, scratch removed", r, {},
               lambda r, s: r["overflow"] and r["returncode"] in FLOOD_EXITS and r["elapsed_s"] < 20
               and not r["wrapper_deadline"] and r["survivors"] == 0 and bool(r["scratch_removed"]))
    finally:
        shutil.rmtree(canary_dir, ignore_errors=True)
        shutil.rmtree(outside_dir, ignore_errors=True)
        for base in ("/tmp", "/var/tmp", "/dev/shm"):
            try:
                (Path(base) / marker).unlink()
            except OSError:
                pass
    return results


def run_unit(unit: str, out_dir: Path) -> dict:
    nonce = f"u{os.getpid()}{unit.replace('-', '')}"
    r = run_contained(unit_inner(unit, nonce), nonce)
    (out_dir / f"{unit}.log").write_bytes(r["output"])
    detail, result, parsed_pass = None, None, False
    try:
        line = next(l for l in reversed(r["output"].splitlines()) if l.startswith(b'{"cases"'))
        result = matrix.validate_result(line, unit)  # also requires every frozen case, in order
        (out_dir / f"{unit}.json").write_bytes(line + b"\n")
        parsed_pass = all(c["status"] == "PASS" for c in result["cases"])
    except (StopIteration, ValueError) as error:
        detail = f"no valid result document: {type(error).__name__}"
    problems = [name for name, bad in (("exit", r["returncode"] != 0), ("overflow", r["overflow"]), ("wrapper_deadline", r["wrapper_deadline"]),
                                       ("survivors", r["survivors"] != 0), ("scratch_not_removed", not r["scratch_removed"])) if bad]
    status = "PASS" if parsed_pass and not problems else "FAIL"
    return {"unit": unit, "status": status, "returncode": r["returncode"], "elapsed_s": r["elapsed_s"],
            "overflow": r["overflow"], "survivors": r["survivors"], "scratch_removed": r["scratch_removed"], "detail": detail,
            "problems": problems, "failed_cases": [c["id"] for c in (result or {"cases": []})["cases"] if c["status"] != "PASS"],
            "result_sha256": hashlib.sha256((out_dir / f"{unit}.json").read_bytes()).hexdigest() if (out_dir / f"{unit}.json").exists() else None}


def artifacts_within_budget(out_dir: Path) -> bool:
    return sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file()) <= ARTIFACT_BYTES


def admit(out_dir: Path) -> int | None:
    """Preflight + controls; returns an exit code when blocked, None when admitted."""
    out_dir.mkdir(parents=True, exist_ok=True)
    gate = preflight()
    (out_dir / "preflight.json").write_text(json.dumps(gate, indent=1))
    if not gate["ready"]:
        print("BLOCKED: native admission preflight failed", gate["checks"])
        return 3
    report = controls()
    (out_dir / "controls.json").write_text(json.dumps(report, indent=1))
    if not controls_complete(report):
        print("BLOCKED: admission control failed", [c["control"] for c in report if not c["pass"]] or "incomplete control set")
        return 3
    print("ADMITTED: all", len(report), "controls passed")
    return None


def ci(out_dir: Path) -> int:
    blocked = admit(out_dir)
    if blocked is not None:
        return blocked
    units = [run_unit(unit, out_dir) for unit in matrix.UNITS]
    (out_dir / "units.json").write_text(json.dumps(units, indent=1))
    lines = ["### O23 acceptance fixture", "| unit | status | s | failed cases |", "|---|---|---|---|"]
    lines += [f"| {u['unit']} | {u['status']} | {u['elapsed_s']} | {', '.join(u['failed_cases']) or u['detail'] or ', '.join(u['problems'])} |" for u in units]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if not artifacts_within_budget(out_dir):
        print("FAIL: artifacts exceed the reserved", ARTIFACT_BYTES, "bytes")
        return 1
    return 0 if all(u["status"] == "PASS" for u in units) else 1


def self_test() -> None:
    matrix.self_test()
    argv = outer_argv(["/py", "x"], "n1", uid=1001, gid=1002)
    assert argv[:3] == ["sudo", "-n", "unshare"] and {"--net", "--pid", "--kill-child", "--mount"} <= set(argv)
    assert argv.count(SCRIPT) == 1 and "$" not in "".join(argv[:argv.index(SCRIPT)])  # script is constant
    assert argv[argv.index(SCRIPT) + 1:argv.index(SCRIPT) + 6] == ["o23", "/run/o23-n1", "1001", "1002", str(ROOT)]
    assert "env" in argv and "-i" in argv and "--no-sandbox" not in " ".join(argv)
    assert unit_inner("api-small", "n1")[-1] == "/run/o23-n1/work"
    assert SCRATCH_BYTES + ARTIFACT_BYTES <= 64 * MIB and TIMEOUT_S <= 120 and STREAM_CAP == MIB
    assert len(REQUIRED_CONTROLS) == len(set(REQUIRED_CONTROLS))
    assert last_json(b"noise\n{\"a\": 1}\ntrailing garbage") == {"a": 1}
    assert last_json(b"x\n") is None and last_json(b"{broken\n") is None
    print("o23_linux_admission self-test ok (source logic only; not native admission)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true")
    group.add_argument("--print-plan", action="store_true", help="print the frozen argv per unit; executes nothing")
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--controls", action="store_true", help="preflight + the ten admission controls only (hosted Linux)")
    group.add_argument("--ci", action="store_true", help="preflight, controls, then every unit (hosted Linux only)")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
    elif args.print_plan:
        print(json.dumps({unit: outer_argv(unit_inner(unit, "NONCE"), "NONCE", uid=1001, gid=1001) for unit in matrix.UNITS}, indent=1))
    elif args.preflight:
        gate = preflight()
        print(json.dumps(gate, indent=1))
        return 0 if gate["ready"] else 3
    else:
        if args.out_dir is None:
            parser.error("--ci/--controls need --out-dir")
        if args.controls:
            blocked = admit(args.out_dir)
            return 0 if blocked is None else blocked
        return ci(args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
