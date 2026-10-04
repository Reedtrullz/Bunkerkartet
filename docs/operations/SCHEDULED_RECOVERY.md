# Scheduled recovery operations — 4 October 2026

The owner authorized execution of the next-priorities plan. Selected defaults:

- Consistent SQLite snapshots at03:40/15:40UTC with up to five minutes jitter;
  the live writer continues running. The exact active digest, commit and named
  data volume are discovered and validated, rather than hard-coded to an old volume.
- OpenSSL CMS AES256 encryption using an RSA4096 recipient certificate.
  Only the public certificate is installed on the VPS. The private key stays
  on the FileVault-enabled owner Mac under an owner-only recovery directory.
- The Mac pulls hourly over the existing verified Racknerd-Deploy SSH alias.
  Only ciphertext and redacted receipts cross SSH. It decrypts transiently,
  verifies archive SHA256, schema/integrity/foreign keys and every table's
  row count/content hash, then signs a receipt with the Mac-only private key and acknowledges those exact
  bytes to the VPS. Hash-only acknowledgements are rejected; the hourly check
  independently revalidates the signed proof.
- Recovery-point target24h; recovery-time target15min. These are operational
  targets, not an availability guarantee. An offline Mac cannot accept fresh
  copies; stale/missing/behind acknowledgements fail the hourly host check.
- Keep backups at least30days. There is no automatic deletion or claimed
  immediate erasure from archives. Each host has a500MiB backup quota and an8MiB ciphertext limit per snapshot.
  Sixty12-hourly snapshots plus bounded64KiB receipts and the next snapshot
  fit within that quota. Larger snapshots require a deliberate capacity increase.
  Creation
  fails with an alert before exhausting it. Manual reviewed erasure is needed
  before reaching the quota. Existing release/rollback archives are excluded.
- Snapshot/freshness failures enter systemd failure state and emit an actionable
  journal alert. Status identifies missing/stale snapshots, missing/stale/behind
  off-host copies, corruption and a failed run newer than the last success.

## Installation

VPS prerequisites already present: Python3.12, Docker, OpenSSL and systemd.
Install `scripts/backup_operations.py` as
`/usr/local/lib/bunkerkartet/backup_operations.py`, root-owned mode0644.
Install the **public** certificate as`/etc/bunkerkartet/backup-recipient.pem`.
Use root-owned mode0600 `/etc/bunkerkartet/backup.json`:

```json
{"directory":"/srv/backups/bunkerkartet-operations","certificate":"/etc/bunkerkartet/backup-recipient.pem"}
```

Install the five `.service`/`.timer` files in`deploy/backup/` under`/etc/systemd/system/`, verify
units, run a first snapshot and qualified off-host copy, then enable the two
`.timer` units. The create unit has an exclusive flock and a five-minute timeout.
The off-host receiver reserves its target exclusively, bounds transfer bytes,
flushes data/receipts and directory entries before signed acknowledgement,
keeps encrypted artifacts on failure and cleans only its transient plaintext.
Neither script replaces a live database or deletes previous backup artifacts.

On the Mac, stable copied helper scripts, configuration, public certificate,
private key, logs and encrypted copies live under
`~/Library/Application Support/Bunkerkartet/`. A launch agent named
`com.reidar.bunkerkartet-backup-pull` invokes the copied receiver hourly and at
load. The private key is mode0600 inside a mode0700 directory. The helper uses
Homebrew Python and OpenSSL, independent of an archived Git worktree. Keep the
private key: losing it makes these encrypted backups unrecoverable. Separate
key escrow remains unarranged; copying the public certificate is not key escrow.

## Recovery qualification

The real-host isolated drill retained the original active volume/image and
used a fresh backup, new staging volume and unused loopback port8001. Restored
schema16 accepts a UID10001 transaction and rollback, retains85 sites, passes
integrity/foreign-key checks and reaches authenticated readiness at the exact
release commit. On4October the measured backup took3.071s and restore-to-ready
9.06s. This qualifies that host/sample, not a total-host-loss recovery SLA.

Before a real restore, explicitly select the recovery point and account for
newer writes. Stage and qualify independently first; do not overwrite the active
volume. Keep the prior image/volume/archive as a compatible recovery pair.
The off-host decrypt/table proof additionally qualifies the owner-held key and
stored ciphertext without reading the live volume.

## Selected workspace and merge policy

Choose15-minute idle lock and60-second hidden-page lock. Bearers remain memory
only; actual owner-token rotation is a separately recorded operation. Permit
Kartverket as an optional provider selected after authentication; no provider
is selected by default, no aerial provider is enabled and no automatic route
provider call is made. Do not interpret this configuration as source/field
verification or a right to distribute imagery.

For this solo-owner repository require up-to-date `test` CI and pull requests,
enforce the rule for administrators, and disallow force pushes, branch deletion
and bypass actors. Require zero human approvals so routine authorized automation
is not blocked by unavailable reviewers. Apply governance last, after this
operations PR is merged and exact-version production checks pass. Host and GitHub
readback receipts, rather than these policy files, establish actual activation.

Physical mobile device, screen-reader, field/source judgment, provider-profile,
photo/raster/publication rights and pilot acceptance remain open. Optional
pilots stay disabled. No catalogue/route/observation erasure is scheduled.

OpenSSL command semantics: [official CMS documentation](https://docs.openssl.org/3.0/man1/openssl-cms/).

## Recipient and hourly receiver setup

Create a new owner-only runtime/recovery directory, then generate the key and
public certificate there with `openssl req -x509 -newkey rsa:4096 -nodes -days
3650 -subj '/CN=Bunkerkartet offline recovery' -keyout private.pem -out
recipient.pem`. Use mode0600 for both files and never replace an existing key
pair without a deliberate rotation plan. Transfer only`recipient.pem` to the
VPS. No private-key contents belong in Git, shell command arguments, logs or
receipts.

Copy both helper scripts into the runtime `bin/` directory. Create mode0600
`pull.json` containing absolute paths for `directory`(runtime/backups),
`private_key`(runtime/recovery/private.pem), and
`certificate`(runtime/recovery/recipient.pem). Render
`deploy/backup/com.reidar.bunkerkartet-backup-pull.plist.example` by replacing
`__RUNTIME_ROOT__` with the actual runtime path, save under
`~/Library/LaunchAgents/`, and validate it with `plutil -lint`. Run the receiver
manually first; only then bootstrap the launch agent in the user's GUI domain.
The example explicitly selects Homebrew Python/OpenSSL paths.

A fresh complete copy must produce local `backups/status.json` with
`verified=true` and `offhost_acknowledged=true`. Server `status` must be ready.
The key recovery receipt contains paths and hashes only; it never contains
key material or catalogue rows. Use a controlled missing-backup fixture with
a separate config to exercise OnFailure; never corrupt an actual archive to
produce the alarm.
