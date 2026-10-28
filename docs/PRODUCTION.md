# FRIMS — Throughput today and the production / global-scale stack

## 1. Measured throughput (2026-08-29, MacBook, single `uvicorn` worker, data in memory)

### Ingest — bounded by the FIRMS API, not by us

| Metric | Value |
|---|---|
| Sustained rows | **~656 rows/s** (130 calls measured) |
| Per call | median 1,616 rows, 3.8 s (includes our 2 s courtesy pause) |
| Cost | ~1 transaction per ~700 rows, budget 5,000 tx / 10 min per MAP_KEY |
| 90-day, 4-sensor live pull (India) | 72 calls ≈ 4 min ≈ 700 tx |
| 3-hourly refresh | ~4 calls ≈ 15 s ≈ 40 tx (all older windows cached) |

### Pipeline — features → rules → sources → ML → GeoPackage

| Run | Detections | Wall time | Throughput |
|---|---|---|---|
| live (cached FIRMS) | 65,363 | 4 s | ~16 k det/s |
| SP historical (both VIIRS, Oct 2025 – May 2026) | 1,180,747 | 34 s | **~35 k det/s** |

Rolling persistence is vectorised (sorted keys + cumulative sums), the site join is one `sjoin_nearest` per
provider, the GeoPackage write is pyogrio. Scaling is roughly linear. One-off costs on a fresh machine: land cover
overview reads (~1 min for India), OSM PBF extraction (~80 s).

### API — Apache Bench, 10 concurrent clients, 60 requests each

| Endpoint | Payload | Req/s | Mean latency |
|---|---|---|---|
| `/api/health` | tiny | 2,694 | 3.7 ms |
| `/api/stats?days=7` | 1 KB | 300 | 33 ms |
| `/api/alerts?days=7` | 2 KB | 217 | 46 ms |
| `/api/detections?days=1` (5 k cap) | 23 KB | 205 | 49 ms |
| `/api/sources` | 551 KB | 34 | 296 ms |
| `/api/detections?days=7` (20 k cap) | 778 KB | 26 | 391 ms |

Dashboard-style calls run at hundreds of req/s; the large GeoJSON endpoints at 25–35 req/s, dominated by JSON
serialisation and ~20 MB/s transfer. Cheap wins if needed: `uvicorn --workers N`, cache GeoJSON per
(dataset, days, end) until the next refresh, gzip, vector tiles for > 50 k points.

## 2. What "global" means in numbers

| Quantity | India today | Global |
|---|---|---|
| Detections / day | ~700 (monsoon) – 15 k (stubble season) | **100 k – 500 k** (4 sensors; one VIIRS sensor alone is ~25 k on a quiet June day) |
| Detections / year | 1–2 M | **~50–150 M** |
| Pipeline at 35 k det/s | 4–34 s | one day ≈ 15 s; a year ≈ 1 h of batch compute |
| Storage (~430 B/detection with features) | 0.5 GB | **~40–60 GB / year** |
| FIRMS transactions | 40 per refresh | 150–700 per global daily pull; a 90-day global backfill ≈ 15 k tx → ~40 min paced by the budget guard |

Compute and budget are fine. What does **not** carry over is the architecture: one pandas frame in RAM, a full
90-day recompute every run, raw GeoJSON to the browser, and a site database built for one country.

## 3. Production stack (same Python code, different plumbing)

| Layer | Now | Production |
|---|---|---|
| Ingest | `FirmsClient` in-process | Scheduled workers (Prefect / Airflow / k8s CronJob) writing raw parquet to **S3-style object storage** partitioned `sensor/date`. FIRMS API for NRT; **NASA LANCE / Earthdata direct downloads** as the bulk path (no per-key transaction cap). |
| Storage | one GeoPackage per dataset | **PostGIS** (detections partitioned by month with GiST index; sites; sources; alerts) for serving, plus a **Parquet / DuckDB** lake for analytics and model training. GeoPackage remains the export format for QGIS users. |
| Features | full 90-day recompute | **Incremental**: per-cell and per-site *daily rollup* tables; 30/90-day statistics become window queries over rollups; only new detections are featurised. This is the one real code change. |
| Site database | 50 seed + WRI + OSM India | **Global Energy Monitor** trackers (power, steel, refineries, LNG, coal mines — curated, global, free), the **EOG / VIIRS Nightfire gas-flare catalogue** (definitive flare list), OSM planet via pyosmium (~1 h). Refreshed monthly. The incident study showed site coverage — not the detector — limits alert recall. |
| Land cover | WorldCover overview reads | unchanged; ~2,600 global tiles, cached per cell once. |
| Classification | rules + GBM | unchanged code; thresholds per country / biome; retrain per region and season on SP `type` truth, which exists globally. |
| Serving | FastAPI, one worker, GeoJSON | FastAPI behind gunicorn/uvicorn workers, **Redis** response cache, **vector tiles** (`martin` / `pg_tileserv`, or nightly PMTiles) instead of GeoJSON, gzip. |
| Frontend | vendored Leaflet | **MapLibre GL** on vector tiles (renders millions of points); same panel design and endpoints. |
| Ops | laptop | Docker images; a single 8-core / 32 GB VM is sufficient for global, Kubernetes only if HA is required; Prometheus + Grafana; alert delivery via webhook / e-mail / SMS; secrets in a vault. NTRO context: everything must run **on-prem / air-gapped** — nothing here depends on SaaS. |

## 4. Limits that are not ours

- **Latency**: FIRMS NRT is ~3 h behind the satellite; sub-minute URT exists only for the US/Canada. Hours-level
  global "real time" is the ceiling unless geostationary feeds (MSG-IODC, INSAT-3D) are obtained directly from
  EUMETSAT / ISRO — a data-agreement question, not software. The FIRMS API does not expose MSG-IODC.
- **Sensor physics**: 375 m pixels, ~4 passes/day. Short or indoor events are invisible regardless of stack (6 of
  14 real incidents left no detection).

## 5. Migration order

1. PostGIS + incremental rollups (unblocks everything else).
2. GEM + EOG flare catalogue as the global site layer (largest accuracy win).
3. Vector tiles + MapLibre.
4. LANCE bulk ingest, then multi-region scheduling and per-region thresholds.

Rough effort: 2–3 weeks to a working global instance, mostly items 1–2. The classifier, evaluation framework and
incident checker carry over unchanged.
