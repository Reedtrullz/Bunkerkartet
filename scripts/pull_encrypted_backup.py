#!/usr/bin/env python3
"""Pull a sealed VPS snapshot; acknowledge only after local decryption/row proof."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

try:
    from scripts.backup_operations import MAX_CIPHER_BYTES, MAX_STORAGE_BYTES, STAMP, attest, canonical, decrypt_and_verify, durable, save
except ModuleNotFoundError:
    from backup_operations import MAX_CIPHER_BYTES, MAX_STORAGE_BYTES, STAMP, attest, canonical, decrypt_and_verify, durable, save


def remote(command, content=None):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "Racknerd-Deploy", command], input=content, capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError("remote operation failed; output withheld")
    return result.stdout


def receive(stream, output, expected_size):
    if not 0 < expected_size <= MAX_CIPHER_BYTES:
        raise ValueError("transfer size exceeds bounds")
    total = 0
    while chunk := stream.read(min(1024 * 1024, expected_size - total + 1)):
        total += len(chunk)
        if total > expected_size:
            raise ValueError("encrypted transfer exceeds receipt size")
        output.write(chunk)
    if total != expected_size:
        raise ValueError("encrypted transfer is truncated")


def pull(config):
    root = Path(config["directory"])
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (root / "pull.lock").open("w") as lock:
        os.chmod(root / "pull.lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = json.loads(remote("sudo -n /usr/bin/python3 /usr/local/lib/bunkerkartet/backup_operations.py latest"))
        stamp = metadata["stamp"]
        if not STAMP.fullmatch(stamp) or not re.fullmatch(r"[0-9a-f]{64}", metadata["cipher_sha256"]) or not 0 < metadata["cipher_size"] <= MAX_CIPHER_BYTES:
            raise ValueError("invalid encrypted snapshot identity")
        if not 0 <= time.time() - metadata["created_at_epoch"] <= 24 * 3600:
            raise ValueError("remote snapshot is stale")
        folder = root / stamp
        folder.mkdir(mode=0o700, exist_ok=True)
        durable(root)
        cipher = folder / "archive.tar.gz.cms"
        if not cipher.exists():
            if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) + metadata["cipher_size"] > MAX_STORAGE_BYTES:
                raise ValueError("off-host disk budget exhausted; no archives deleted")
            # Receive into a run-owned file; never overwrite an existing encrypted snapshot.
            with tempfile.NamedTemporaryFile(prefix=".incoming-", dir=folder, delete=False) as output:
                temporary = Path(output.name)
                process = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "Racknerd-Deploy", "sudo -n cat /srv/backups/bunkerkartet-operations/" + stamp + "/archive.tar.gz.cms"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                try:
                    receive(process.stdout, output, metadata["cipher_size"])
                    output.flush()
                    os.fsync(output.fileno())
                    if hasattr(fcntl, "F_FULLFSYNC"):
                        fcntl.fcntl(output.fileno(), fcntl.F_FULLFSYNC)
                    if process.wait(timeout=180) or temporary.stat().st_size != metadata["cipher_size"]:
                        raise ValueError("encrypted transfer did not complete")
                    # Link reserves exclusively; concurrent or old files are not replaced.
                    os.link(temporary, cipher)
                    durable(folder)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait()
                    temporary.unlink(missing_ok=True)
        summary = decrypt_and_verify(cipher, metadata, config["private_key"], config["certificate"], root)
        durable(cipher)
        durable(folder)
        durable(config["private_key"])
        durable(Path(config["private_key"]).parent)
        save(folder / "receipt.json", metadata)
        checksum = metadata["cipher_sha256"]
        proof = attest(metadata, summary, config["private_key"], root)
        acknowledgement = json.loads(remote("sudo -n /usr/bin/python3 /usr/local/lib/bunkerkartet/backup_operations.py acknowledge --stamp " + stamp + " --sha256 " + checksum + " --proof-stdin", canonical(proof)))
        receipt = {"verified": True, "stamp": stamp, "cipher_sha256": checksum, "created_at_epoch": metadata["created_at_epoch"], "verified_at_epoch": time.time(), "schema": summary["schema"], "tables_verified": len(summary["tables"]), "offhost_acknowledged": acknowledgement["decrypted_rows_verified"]}
        save(root / "status.json", receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = None
    try:
        config = json.loads(Path(args.config).read_text())
        print(json.dumps(pull(config), sort_keys=True))
        return 0
    except Exception:
        if config:
            root = Path(config["directory"])
            if root.is_dir():
                save(root / "failure.json", {"failed": True, "at_epoch": time.time()})
        print("off-host backup verification failed; private data and command output withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
