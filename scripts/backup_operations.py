#!/usr/bin/env python3
"""Host-side encrypted snapshots and independently verified off-host recovery."""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import sys

MAX_CIPHER_BYTES = 8 * 1024 * 1024
MAX_STORAGE_BYTES = 500 * 1024 * 1024
STAMP = re.compile(r"\d{8}T\d{6}Z")


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError("backup command failed; command output withheld")
    return result.stdout


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path = Path(path)
    encoded = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("backup receipt exceeds bounds")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
        if hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(stream.fileno(), fcntl.F_FULLFSYNC)
    os.replace(temporary, path)
    durable(path.parent)


def durable(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        if not path.is_dir() and hasattr(fcntl, "F_FULLFSYNC"):
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    finally:
        os.close(fd)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def attest(metadata, summary, private_key, directory):
    if summary != metadata["database"]:
        raise ValueError("decryption proof does not match snapshot")
    payload = {"stamp": metadata["stamp"], "cipher_sha256": metadata["cipher_sha256"], "database_proof_sha256": hashlib.sha256(canonical(summary)).hexdigest(), "decrypted_rows_verified": True, "verified_at_epoch": int(time.time())}
    with tempfile.TemporaryDirectory(prefix=".attestation-", dir=directory) as folder:
        message = Path(folder) / "proof.json"
        signature = Path(folder) / "signature.bin"
        message.write_bytes(canonical(payload))
        command(["openssl", "dgst", "-sha256", "-sign", str(private_key), "-out", str(signature), str(message)])
        return {"payload": payload, "signature": base64.b64encode(signature.read_bytes()).decode()}


def validate_attestation(proof, metadata, certificate, directory):
    if not isinstance(proof, dict) or certificate is None:
        raise ValueError("signed off-host attestation is required")
    payload = proof["payload"]
    if set(payload) != {"stamp", "cipher_sha256", "database_proof_sha256", "decrypted_rows_verified", "verified_at_epoch"} or payload["stamp"] != metadata["stamp"] or payload["cipher_sha256"] != metadata["cipher_sha256"] or payload["decrypted_rows_verified"] is not True or payload["database_proof_sha256"] != hashlib.sha256(canonical(metadata["database"])).hexdigest() or digest(certificate) != metadata["recipient_sha256"]:
        raise ValueError("off-host attestation does not match snapshot")
    if not isinstance(payload["verified_at_epoch"], int) or not metadata["created_at_epoch"] - 1 <= payload["verified_at_epoch"] <= time.time() + 300:
        raise ValueError("off-host attestation time is invalid")
    signature_bytes = base64.b64decode(proof["signature"], validate=True)
    if not 128 <= len(signature_bytes) <= 8192:
        raise ValueError("invalid attestation signature size")
    with tempfile.TemporaryDirectory(prefix=".verify-attestation-", dir=directory) as folder:
        message = Path(folder) / "proof.json"
        signature = Path(folder) / "signature.bin"
        public = Path(folder) / "public.pem"
        message.write_bytes(canonical(payload)); signature.write_bytes(signature_bytes)
        public.write_bytes(command(["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"]))
        command(["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signature), str(message)])


def live_source(inspect):
    image = inspect["Config"]["Image"]
    if not re.fullmatch(r"ghcr.io/reedtrullz/bunkerkartet@sha256:[0-9a-f]{64}", image):
        raise ValueError("active image must be immutable")
    versions = [item[12:] for item in inspect["Config"]["Env"] if item.startswith("APP_VERSION=")]
    mounts = [item for item in inspect["Mounts"] if item["Destination"] == "/app/data"]
    if len(versions) != 1 or not re.fullmatch(r"[0-9a-f]{40}", versions[0]):
        raise ValueError("active version must be a full commit")
    if len(mounts) != 1 or mounts[0]["Type"] != "volume" or not re.fullmatch(r"[a-zA-Z0-9_-]+", mounts[0]["Name"]):
        raise ValueError("active volume is ambiguous")
    return image, versions[0], mounts[0]["Name"]


def database_summary(archive, directory, expected_version):
    with tempfile.TemporaryDirectory(prefix=".database-proof-", dir=directory) as folder:
        path = Path(folder) / "snapshot.sqlite3"
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            if len(members) != 1 or members[0].name != "bunkerkartet.sqlite3" or not members[0].isfile() or members[0].size > 500 * 1024 * 1024:
                raise ValueError("unexpected archive members")
            with source.extractfile(members[0]) as incoming, path.open("xb") as output:
                shutil.copyfileobj(incoming, output)
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != expected_version or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("restored database qualification failed")
            tables = {}
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                quoted = '"' + name.replace('"', '""') + '"'
                rows = sorted(repr(tuple(row)) for row in connection.execute("SELECT * FROM " + quoted))
                tables[name] = {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
            return {"schema": expected_version, "tables": tables}


def encrypt_snapshot(archive, backup_receipt, certificate, destination, *, stamp, created_at):
    if not STAMP.fullmatch(stamp) or backup_receipt.get("verified") is not True or backup_receipt["sha256"] != digest(archive):
        raise ValueError("invalid snapshot receipt")
    destination = Path(destination)
    target = destination / "archive.tar.gz.cms"
    if target.exists() or (destination / "receipt.json").exists():
        raise ValueError("encrypted destination exists")
    summary = database_summary(archive, destination, backup_receipt["database_schema_version"])
    temporary = destination / ".cipher.tmp"
    try:
        command(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-in", str(archive), "-out", str(temporary), "-outform", "DER", str(certificate)])
        if not 0 < temporary.stat().st_size <= MAX_CIPHER_BYTES:
            raise ValueError("encrypted archive exceeds bounds")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    metadata = {"receipt_version": 1, "stamp": stamp, "created_at_epoch": created_at, "cipher_sha256": digest(target), "cipher_size": target.stat().st_size, "backup": backup_receipt, "database": summary, "recipient_sha256": digest(certificate)}
    save(destination / "receipt.json", metadata)
    return metadata


def decrypt_and_verify(cipher, metadata, private_key, certificate, directory):
    if metadata.get("receipt_version") != 1 or not 0 < metadata["cipher_size"] <= MAX_CIPHER_BYTES or Path(cipher).stat().st_size != metadata["cipher_size"] or digest(cipher) != metadata["cipher_sha256"] or digest(certificate) != metadata["recipient_sha256"]:
        raise ValueError("encrypted receipt mismatch")
    with tempfile.TemporaryDirectory(prefix=".decrypted-", dir=directory) as folder:
        archive = Path(folder) / "archive.tar.gz"
        command(["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(cipher), "-recip", str(certificate), "-inkey", str(private_key), "-out", str(archive)])
        if archive.stat().st_size > 100 * 1024 * 1024 or digest(archive) != metadata["backup"]["sha256"]:
            raise ValueError("decrypted archive checksum mismatch")
        summary = database_summary(archive, folder, metadata["backup"]["database_schema_version"])
        if summary != metadata["database"]:
            raise ValueError("off-host rows do not match snapshot")
        return summary


def status(root, *, now=None, certificate=None):
    root = Path(root)
    now = time.time() if now is None else now
    problems = []
    latest = json.loads((root / "latest.json").read_text()) if (root / "latest.json").exists() else None
    offhost = json.loads((root / "offhost.json").read_text()) if (root / "offhost.json").exists() else None
    if latest is None:
        problems.append("missing-local-backup")
    elif not 0 <= now - latest["created_at_epoch"] <= 24 * 3600:
        problems.append("stale-local-backup")
    if latest:
        stamp = latest["stamp"]
        cipher = root / stamp / "archive.tar.gz.cms"
        if not STAMP.fullmatch(stamp) or not cipher.is_file() or cipher.stat().st_size > MAX_CIPHER_BYTES or digest(cipher) != latest["cipher_sha256"]:
            problems.append("local-cipher-missing-or-corrupt")
    if offhost is None:
        problems.append("missing-offhost-copy")
    elif not 0 <= now - offhost["created_at_epoch"] <= 24 * 3600:
        problems.append("stale-offhost-copy")
    elif latest and latest["cipher_sha256"] != offhost["cipher_sha256"]:
        problems.append("offhost-copy-behind")
    if offhost:
        try:
            if not STAMP.fullmatch(offhost["stamp"]) or offhost.get("decrypted_rows_verified") is not True:
                raise ValueError("unqualified off-host copy")
            metadata = json.loads((root / offhost["stamp"] / "receipt.json").read_text())
            validate_attestation(offhost.get("proof"), metadata, certificate, root)
        except (ValueError, OSError, KeyError, TypeError):
            problems.append("invalid-offhost-proof")
    if (root / "failure.json").exists() and (latest is None or json.loads((root / "failure.json").read_text())["at_epoch"] > latest["created_at_epoch"]):
        problems.append("newer-backup-failure")
    return {"ready": not problems, "problems": problems, "latest": latest, "offhost": offhost}


def acknowledge(root, stamp, checksum, *, proof=None, certificate=None):
    if not STAMP.fullmatch(stamp):
        raise ValueError("invalid snapshot identity")
    root = Path(root)
    folder = root / stamp
    metadata = json.loads((folder / "receipt.json").read_text())
    if metadata["stamp"] != stamp or checksum != metadata["cipher_sha256"] or digest(folder / "archive.tar.gz.cms") != checksum:
        raise ValueError("off-host acknowledgement mismatch")
    validate_attestation(proof, metadata, certificate, root)
    current = json.loads((root / "offhost.json").read_text()) if (root / "offhost.json").exists() else None
    if current and current["created_at_epoch"] > metadata["created_at_epoch"]:
        raise ValueError("off-host acknowledgement cannot go backwards")
    result = {"stamp": stamp, "created_at_epoch": metadata["created_at_epoch"], "cipher_sha256": checksum, "acknowledged_at_epoch": time.time(), "decrypted_rows_verified": True, "proof": proof}
    save(root / "offhost.json", result)
    return result


def create(config):
    root = Path(config["directory"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 2 * 1024**3 or sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) >= MAX_STORAGE_BYTES - MAX_CIPHER_BYTES:
        raise ValueError("backup disk budget exhausted; no archives deleted")
    inspect = json.loads(command(["docker", "inspect", "bunkerkartet-bunkerkartet-1"]))[0]
    image, commit, volume = live_source(inspect)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    folder = root / stamp
    folder.mkdir(mode=0o700)
    # Only run-owned plaintext is transient. Existing release archives are never touched.
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=folder) as plain:
        archive = Path(plain) / "archive.tar.gz"
        command(["docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp:rw,nosuid,noexec,size=32m", "--user", "0:0", "--volume", volume + ":/source", "--volume", plain + ":/proof", "--entrypoint", "python", image, "/app/scripts/backup_database.py", "create", "--database", "/source/bunkerkartet.sqlite3", "--archive", "/proof/archive.tar.gz", "--max-archive-bytes", str(MAX_CIPHER_BYTES - 16 * 1024)])
        backup = json.loads(Path(str(archive) + ".receipt.json").read_text())
        metadata = encrypt_snapshot(archive, backup, config["certificate"], folder, stamp=stamp, created_at=time.time())
        metadata.update({"image": image, "commit": commit, "volume": volume})
        save(folder / "receipt.json", metadata)
    save(root / "latest.json", {key: metadata[key] for key in ("stamp", "created_at_epoch", "cipher_sha256")})
    return {"created": True, "stamp": stamp, "cipher_sha256": metadata["cipher_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "status", "latest", "acknowledge"))
    parser.add_argument("--config", default="/etc/bunkerkartet/backup.json")
    parser.add_argument("--stamp")
    parser.add_argument("--sha256")
    parser.add_argument("--proof-stdin", action="store_true")
    args = parser.parse_args()
    root = None
    try:
        config = json.loads(Path(args.config).read_text())
        root = Path(config["directory"])
        if args.action == "create":
            result = create(config)
        elif args.action == "status":
            result = status(root, certificate=config["certificate"])
        elif args.action == "latest":
            latest = json.loads((root / "latest.json").read_text())
            result = json.loads((root / latest["stamp"] / "receipt.json").read_text())
        else:
            raw = sys.stdin.buffer.read(64 * 1024 + 1) if args.proof_stdin else b""
            if len(raw) > 64 * 1024:
                raise ValueError("attestation exceeds bounds")
            result = acknowledge(root, args.stamp or "", args.sha256 or "", proof=json.loads(raw) if raw else None, certificate=config["certificate"])
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("ready", True) else 1
    except Exception:
        if root and root.is_dir() and args.action == "create":
            save(root / "failure.json", {"at_epoch": time.time(), "status": "failed"})
        print("backup operation failed; secrets and command output withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
