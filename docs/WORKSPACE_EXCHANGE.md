# Private workspace exchange

The exchange format moves one Bunkerkartet workspace into a separately staged directory for review. It does not merge records into a running app or replace an existing directory. Operational backups remain the recovery mechanism for a stopped-writer restore.

## Export

Run from a compatible repository checkout and choose a private destination for the archive:

```sh
.venv/bin/python scripts/export_workspace.py \
  --database data/bunkerkartet.sqlite3 \
  --seed app/content/site_enrichment.json \
  --archive /private/path/bunkerkartet-workspace.bkws
```

The exporter reads the SQLite database through a read-only connection and SQLite's consistent backup API. It includes the current seed file and checks that the seed bytes did not change during the snapshot. The database retains effective content overrides, import and edit history, observations, routes, provenance, and other application records. The archive records the current database schema version, Git `HEAD` SHA, a digest of the current `app/` and `scripts/` source files, seed identity, table row counts, and SHA-256 plus byte size for each payload member.

The archive has exactly three members: `manifest.json`, `workspace.sqlite3`, and `seed/site_enrichment.json`. Export is limited to 100 MiB compressed and 500 MiB expanded by default. The archive is created with owner-only permissions and an existing output path is an error.

## Verify and stage

```sh
.venv/bin/python scripts/import_workspace.py \
  --archive /private/path/bunkerkartet-workspace.bkws \
  --destination /private/path/bunkerkartet-staged
```

The destination must not already exist. Before creating it, the importer checks the exact member allowlist, path safety, archive and expanded size limits, manifest and schema versions, all member hashes and sizes, seed identity and structure, SQLite integrity and foreign keys, current schema requirements, and table counts. It then creates a private staging tree with `data/bunkerkartet.sqlite3`, `app/content/site_enrichment.json`, and the manifest. Files are owner-only and directories are private to the owner.

The staged tree is a data payload for an isolated compatible checkout. Review it and deliberately install it into a separate app environment if desired. Import never selects a live database, applies a merge, or overwrites an existing destination. The importer checks that the recorded build SHA is well-formed and retains it for review; schema compatibility is checked against the running code's dynamic `CURRENT_SCHEMA_VERSION`.

## Privacy and trust

Workspace archives can contain private curator notes, observations, evidence excerpts and URLs, history, and route details. Keep the archive and staged directory in private storage, and remove them only under the workspace owner's retention policy. The format explicitly excludes `.env` files, environment configuration, tokens and credentials, and every file outside the three-member allowlist. It does not include app code or arbitrary repository files.

SHA-256 checks detect missing, changed, or corrupted members when the manifest remains trustworthy. The archive is not signed or encrypted, so a party able to rewrite the archive can also rewrite its manifest; checksums do not establish who created it. Protect archive access and verify its origin through a separate trusted channel before using its contents.


To verify the staged payload through a separately configured compatible app,
set `BUNKERKARTET_DATA_DIR` to its `data` directory and
`BUNKERKARTET_ENRICHMENT_PATH` to its `app/content/site_enrichment.json`.
The seed path is per app instance; another instance retains its own default.
Keep this qualification private on an unused loopback port and supply a new
synthetic credential. This does not install, merge, or promote the payload.
