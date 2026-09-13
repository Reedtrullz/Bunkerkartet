# Bunkerkartet core v1 implementation plan

## Objective

Build the smallest private, usable war-history map for Trondheim: import external LLM/research JSON packages, review candidate sites, display uncertainty and evidence on a Leaflet/Kartverket map, and plan walking routes with GPX export.

## Global constraints

- Python web service with FastAPI, SQLite, and a small vanilla JS frontend.
- The app does not scrape source sites and does not call an LLM.
- Imported records are candidates until reviewed; never overwrite trusted fields from an import.
- Include bunkers, caves, tunnels, batteries, military sites, POW camps, war memorials, airfields, submarine bases, fortresses, and related remains.
- Exclude `snublesteiner` records by site name/type.
- Preserve source URLs, short excerpts, dates when known, location basis, confidence, uncertainty, access, condition, and warnings.
- Do not store secrets in the repository or browser source.
- Keep route-provider keys server-side and never present a route as permission to enter land or structures.
- Keep the implementation compact. No app-owned scraper, background worker, user accounts, public submissions, offline maps, native app, PostGIS, or automatic stop optimization in core v1.

## Tasks

### Task 1 - foundation (controller)

Create the FastAPI application, configuration, SQLite bootstrap/migrations, authentication guard, health/version endpoints, static frontend shell, dependency files, and local run instructions.

### Task 2 - import contract (delegated)

Implement pure import models and validation with tests. The interface must accept a versioned JSON envelope with stable external keys, site kind, optional WGS84 geometry, precision/uncertainty, location basis, candidate status, access, and one or more source evidence entries. It must reject malformed URLs, invalid coordinates, missing provenance, and `snublestein` records without invented defaults.

### Task 3 - persistence and review (controller)

Persist sites, sources, evidence, import batches, and import records. Add preview/commit endpoints with idempotency, duplicate warnings, candidate-only imports, protected edits, and accept/reject/merge/move operations.

### Task 4 - map and field workflow (controller)

Build the Leaflet/Kartverket map, status/type filters, uncertainty circles, site detail panel, candidate review panel, manual route selection, current-location support, GPX export, and responsive layout.

### Task 5 - routing (controller)

Add a server-side openrouteservice `foot-hiking` proxy with clear provider errors, explicit waypoint order, off-network warnings, route geometry, distance/duration, and GPX output.

### Task 6 - deployment (delegated)

Add Docker, Compose, GitHub CI, immutable GHCR image publication, Ansible deployment scaffolding, loopback-only binding, health/version verification, persistent SQLite volume, backup/restore commands, and Caddy instructions. Do not deploy or push from this task.

### Task 7 - verification (controller/reviewer)

Run unit/API tests, browser smoke checks, Docker runtime checks, import fixture checks, GPX validation, security checks, and a fresh-clone run. Review the full diff for scope and non-claims.

## Definition of done

- A fresh checkout starts locally with documented commands.
- An external JSON batch can be uploaded, previewed, committed without duplicates, and reviewed.
- Approved sites display on the map with source trail and uncertainty.
- A route can be created through selected sites and exported as GPX.
- CI and Docker checks pass; deployment is prepared but not externally published.
