# Illustrated India Session Handoff

## Project Goal

Build and serve a production-ready illustrated map of all India with four visual modes:

- Grand Theft Auto: San Andreas
- Need for Speed: Payback
- Red Dead Redemption 2
- Elden Ring

The runtime product is a static MapLibre application backed by PMTiles archives, local glyphs, a thin search/routing API, and no tile server or PostGIS database.

## Machine

The active machine is Ubuntu 22.04 x86_64, not the separate Mac Studio.

- Intel i7-12700T
- 20 logical CPUs
- 31 GiB RAM
- 2 GiB swap
- 234 GiB NVMe
- Docker 29.7.2
- Default asset root: `~/StylizedMapsAssets`

## Current Release

```text
india-16bbe5794017
```

Web root:

```text
/home/princepatel/StylizedMapsAssets/output/india-16bbe5794017/web
```

Artifacts:

```text
core/vector.pmtiles      1,711,241,376 bytes   z4-14
core/boundary.geojson          364,996 bytes   Survey of India outline, 154 parts / 9,488 points
rdr/terrain.pmtiles      4,730,744,494 bytes   z4-12  (native z12)
elden/base.pmtiles      21,524,080,398 bytes   z4-13  (native z13)
fonts                      about 68 MiB
```

`scripts/map-assets verify` passes against `SHA256SUMS`. The previous release `india-e0b01e3ce720` was deleted; its raster layers were provably wrong (see below).

## The Metatile Extent Bug

The previous release had visible artifacts: horizontal banding at regular intervals, rectangular blocks of missing raster near coastlines, and north-south smeared terrain texture.

Root cause, in `pipeline/renderers/render_india.py`, in both `render_metatile` and `intersects_boundary`:

```python
west, south, _, north = tile_bounds(x, y + height - 1, zoom)   # north read off the BOTTOM row
_, _, east, _         = tile_bounds(x + width - 1, y, zoom)
```

`north` came from the bottom-row tile instead of the top-row tile. For a 4x4 metatile at z12 the extent was 39,136 m wide by 9,784 m tall instead of square — only the southernmost quarter of the geography, warped across the full canvas.

Consequences:

- Terrain stretched 4:1 north-south.
- Hard discontinuities at every metatile row boundary.
- The boundary cutline clipped against the wrong extent, so coastlines were clipped at metatile granularity.
- `intersects_boundary` tested only the bottom row, so metatiles whose upper rows covered land were skipped entirely, leaving rectangular holes.

Fix: read `north` from the top row in both functions.

```python
west, south, _, _     = tile_bounds(x, y + height - 1, zoom)
_, _, east, north     = tile_bounds(x + width - 1, y, zoom)
```

Metatile counts rose after the fix (RDR 2,797 to 2,914; Elden 10,774 to 11,013) because previously-skipped land metatiles now qualify.

### Verification method

`/tmp/.../scratchpad/pmtiles_read.py` is a minimal PMTiles v3 reader used to measure tile-edge continuity directly from the archives. Two metrics, sampled at Udaipur:

| Metric | Old build | New build |
|---|---|---|
| RDR vertical seam max/median | 13.0x | 1.5x |
| RDR horizontal seam max/median | 1.2x | 1.3x |
| Elden vertical seam max/median | 5.7x | 1.6x |
| Elden texture anisotropy (v:h) | 4.4:1 | 1.0 |

Horizontal seams were always flat, which is the signature of the bug: `west` and `east` were correct, only `north` was wrong. Anisotropy on flat terrain (Gangetic plain, Deccan) is now 1.0. Worth re-running this measurement after any future raster change.

## Asset Pipeline

### Sources

- Survey of India external boundary
- Geofabrik India OSM snapshot dated 2026-08-12
- Copernicus GLO-30 DEM (396 one-degree tiles, about 14 GiB)
- ESA WorldCover 2021 (67 three-degree tiles, about 4.9 GiB)
- OpenFreeMap Noto glyph PBFs
- Planetiler 0.10.2

The official Survey of India URL fails TLS validation and then returns HTTP 403; bootstrap falls back to a commit-pinned mirror.

### Zoom coverage

`DISPLAY_MIN_ZOOM = 4` in `build_assets.py` drives both the catalog minimum and the `gdaladdo` overview factors:

```python
overviews = [str(1 << level) for level in range(1, zoom - DISPLAY_MIN_ZOOM + 1)]
```

RDR gets 2..256, Elden 2..512. Previously these were hardcoded and bottomed out at z8, so z4-z7 had no ground at all.

### Boundary as a shipped product

`package()` copies the preflight GeoJSON to `core/boundary.geojson`, exposes it as `products.boundary` in the catalog, and includes it in the artifact manifest. The manifest product loop now covers all files in the staging directory, not just `*.pmtiles`.

### Resource profile

`runtime/resources.json` holds `cpuLimit`, `memoryLimitGiB`, `rasterWorkers`.

Measured Elden cost is **1.2 GiB anonymous memory per worker**, read from the container cgroup (`memory.stat` anon, not `free`, which counts reclaimable page cache). The pipeline's derived cap `min(rasterWorkers, memoryLimitGiB - 3)` assumes roughly 1 GiB per worker plus 3 GiB base, which is too optimistic:

| Config | Ratio | Result |
|---|---|---|
| 9 workers / 12 GiB | 1.33 | completed |
| 15 workers / 18 GiB | 1.20 | OOM killed (exit 137) at 44% |
| 10 workers / 18 GiB | 1.80 | completed |

Pin `rasterWorkers` explicitly rather than letting it derive from memory. Note that `scripts/map-assets resources <cpus> <gib>` overwrites `rasterWorkers` with the machine's total CPU count as a side effect — re-pin after using it.

An OOM is cheap: every committed metatile is recorded in `completed_jobs`, and `resume` pins `MAP_BUILD_ID` from `status.json` and skips completed work.

### Build ID fingerprinting

`fingerprint()` hashes `config/regions/india.json`, `config/sources.lock.json`, `pipeline/acquire_india.py`, `pipeline/renderers/render_tracer.py`, `pipeline/renderers/render_india.py`, `pipeline/build_assets.py`, `scripts/build-vector`, and `planetiler/*.java`. **Editing any of these mid-build changes the build ID**, so a later `resume` would orphan the running build's checkpoints. Frontend and server files are not hashed and are safe to edit during a build.

### Disk

A full run peaks around 50 GiB beyond the sources: during `package` both the mbtiles and the converted pmtiles exist simultaneously. Preflight requires 80 GiB free (`MAP_ASSET_MIN_FREE_GB`). Set `MAP_KEEP_SOURCES=1` to retain the DEM and WorldCover tiles, otherwise `cleanup` deletes them and the next re-render costs a ~20 GiB download.

## Frontend

Framework-free TypeScript with MapLibre and PMTiles.

### Layer stack

The base style (`web/src/renderers/nfs/style.ts`) is the parent of all four renderers; each derived renderer remaps layers by id.

- `sea` — background, the out-of-country water colour
- `land` — fill from the `boundary` GeoJSON source
- `coastline` — line from the same source
- raster layers (`rdr-terrain`, `elden-base`) splice in immediately after `land`, located by `findIndex`, not a fixed index

Sea colours per renderer: NFS `#060c10`, San Andreas `#4d7fa3`, RDR `#b09a72`, Elden `#6f6448`. These were chosen by eye and are worth a design review.

### Camera bounds

`maxBounds` uses an 18-degree margin around the region bounds. MapLibre refuses to zoom out past the point where the viewport is taller than the bounds, so an exact-bbox `maxBounds` made it impossible to see the whole country — it clamped at about z5.5 with Kerala and Kashmir cut off. With the margin the widest view is about z4.4.

### Other

- Self-hosted fonts in `web/src/fonts/` (Special Elite 400, Work Sans variable 400-600), emitted and hashed by Vite. No external font requests remain.
- `build.copyPublicDir: false` — map archives are served from the release web root, never bundled. Without this, `vite build` copied the entire 21 GiB release into `dist` and took over four minutes.
- `manualChunks` splits MapLibre (941 KB) and pmtiles into their own chunks; app code is about 28 KB.
- The zoom readout is initialised at startup; it was a hardcoded `z 9.4` placeholder in `index.html` that only updated on the first zoom event.

Key files:

```text
web/src/main.ts
web/src/api.ts
web/src/search.ts
web/src/navigation.ts
web/src/compass.ts
web/src/styles.css
web/src/renderers/*/style.ts
web/src/map/catalog.ts
index.html
```

## Backend

Small Fastify TypeScript API keeping provider credentials off the client.

```text
GET /api/v1/health
GET /api/v1/search?q=...&limit=...&proximity=lng,lat
GET /api/v1/routes/driving?start=lng,lat&end=lng,lat
```

Behaviour: anonymous public API, additive-only `/api/v1` contract, India coordinate validation, ORS Pelias autocomplete constrained to `IND`, ORS `driving-car` directions, 8s search timeout, 15s route timeout, 60 req/min search and 20 req/min route rate limits, canonical error shape with machine code and request ID.

### Provider content negotiation

Routing posts to `/v2/directions/driving-car/geojson`, which answers `application/geo+json` and enforces negotiation. The request originally sent `accept: application/json` and every live route call returned **HTTP 406**. The mocked contract tests passed because the mock does not negotiate content. `app.test.ts` now asserts the Accept header.

### Logging

`buildApp` takes an optional `logger` (default `false`, so tests stay silent). `server/index.ts` passes `{level: process.env.LOG_LEVEL ?? "info"}`, giving pino JSON with `reqId` and `responseTime` in production. SIGTERM and SIGINT close the server before exit so `docker stop` drains connections.

Required secret: `OPENROUTESERVICE_API_KEY`.

## VPS Deployment

Two containers: Caddy serves the frontend, fonts, catalog and PMTiles; Node/Fastify proxies OpenRouteService.

```text
Dockerfile.web
Dockerfile.api
Caddyfile
compose.production.yaml
vite.config.ts
```

`Dockerfile.web` must copy `vite.config.ts` — without it the image builds with default Vite config, losing chunk splitting and `copyPublicDir: false`.

Caddy: automatic HTTPS, SPA fallback, `/api/*` reverse proxy, `no-cache` on `/maps/current.json`, one-year immutable caching for releases and hashed assets, PMTiles byte ranges with identity encoding, and security headers (CSP, HSTS, `frame-ancestors 'none'`, `base-uri 'self'`, nosniff, Referrer-Policy, `Permissions-Policy: geolocation=(self)`, `Server` stripped).

Deploy:

```bash
export OPENROUTESERVICE_API_KEY="..."
export DOMAIN="maps.example.com"
export MAP_WEB_ROOT="$HOME/StylizedMapsAssets/output/india-16bbe5794017/web"
docker compose -f compose.production.yaml up -d --build
```

This host runs Tailscale on 443, so local validation overrides ports to 8080 and sets `DOMAIN=":80"`.

## Verification Evidence

- `npm run build` and `npm run test:api` (3/3) pass
- `scripts/map-assets verify` passes on the 27 GiB release
- Seam and anisotropy measurements confirm the metatile fix (table above)
- All three PMTiles archives report `minzoom=4`
- Production compose stack built and validated on :8080 — app shell, catalog `no-cache`, PMTiles `206` with `identity` encoding and matching manifest totals, self-hosted glyphs, all security headers, immutable hashed assets, clean shutdown
- Live OpenRouteService calls with a real key: search returns normalized Indian results; Delhi to Mumbai routes at 1,348.3 km / 14.0 h / 54 steps; Mumbai to Pune through the containerized stack at 146.6 km / 44 steps
- Browser: whole-country view at z4.4 renders terrain, coastline and the Andamans in all four renderers

## Known Gaps

- Deploy to a real domain and test GPS over HTTPS on a phone
- Sea colours and coastline weights are unreviewed design choices
- No frontend unit or E2E tests in the repository
- Voice guidance, wake lock, and background navigation are not implemented
- API has structured logging but no metrics endpoint
- The Mac pipeline files (`pipeline/build_assets_macos.py`, `scripts/map-assets-mac`) have never been run end to end

## Worktree State

The working tree is intentionally dirty. Review and commit intended files in logical groups only when explicitly requested. Do not revert unrelated untracked `Readme.md`.
