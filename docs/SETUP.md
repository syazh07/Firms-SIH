# FRIMS — Setup & Run Guide

Everything below was tested on macOS (Apple Silicon) with Python 3.12 managed by `uv`. Linux works the same way.

## 1. Prerequisites

| Need | Why | Get it |
|---|---|---|
| `uv` | Python + dependency manager (installs Python 3.12 itself) | `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| NASA FIRMS **MAP_KEY** | every FIRMS API call needs it (free, 5000 transactions / 10 min) | https://firms.modaps.eosdis.nasa.gov/api/map_key |
| ~3 GB disk | Geofabrik India OSM extract (1.7 GB) + generated GeoPackages | — |
| Internet | only for the *first* data pull; afterwards everything runs from cache | — |

No Docker, no PostGIS, no Node build step. QGIS is optional (to open the GeoPackages).

## 2. Install

```bash
git clone <repo> frims && cd frims
uv sync --group dev                      # creates .venv with Python 3.12 and all deps (geopandas, rasterio, sklearn, fastapi, pyosmium …)
echo "MAP_KEY=<your 32-char firms key>" > .env
uv run python -c "from frims.ingest.firms import FirmsClient; print(FirmsClient().status())"
# -> TxStatus(used=0, limit=5000, interval='10 minutes')   ✓ key works
```

## 2b. What a fresh clone already contains

The repository (private) ships the inputs that are slow or need a login to re-obtain, so a fresh clone needs
**no FIRMS transactions and no Earthdata/GEM downloads** to reproduce the current datasets:

| Committed | Size | Regenerating it would cost |
|---|---|---|
| `data/raw/firms/` — every 5-day FIRMS window (NRT + SP) | 80 MB | ~2,200 transactions |
| `data/raw/flares/` — EOG Nightfire flare survey | 21 MB | Earthdata sign-in |
| `data/raw/gem/` — GEM coal-mine tracker (Aug 2026) | 3 MB | download form |
| `data/cache/osm_sites.gpkg` — 43k OSM industrial features | 19 MB | 1.6 GB PBF download + 82 s |
| `data/cache/landcover_cells.parquet`, Natural Earth, India boundary, WRI | 7 MB | a few minutes online |
| `models/gbm.joblib` + JSON reports | 2 MB | `python -m frims.classify.evaluate` |

Not committed (rebuild in minutes): `data/frims_*.gpkg`, `data/truth/`, `data/raw/osm/india-latest.osm.pbf`.
Rebuild both datasets offline with:

```bash
uv run python scripts/run_pipeline.py --backfill-days 90 --offline
uv run python scripts/run_pipeline.py --dataset sp2025_26 --source-set sp --start 2025-10-15 --end 2026-05-31
```

## 3. Quick start (live dataset, ~5 minutes, ~700 FIRMS transactions)

```bash
uv run python scripts/run_pipeline.py --backfill-days 90 --dry-run     # shows the 72 planned calls + tx estimate
uv run python scripts/run_pipeline.py --backfill-days 90               # FIRMS → clip → sites → features → rules → ML → data/frims_live.gpkg
uv run uvicorn frims.api.app:app --host 127.0.0.1 --port 8000          # API + web UI
open http://127.0.0.1:8000
```

What the first run does, in order (each step is cached so re-runs are fast and free):

1. Pulls VIIRS S-NPP / NOAA-20 / NOAA-21 and MODIS NRT detections for India, 90 days, in 5-day windows → `data/raw/firms/`
2. Downloads Natural Earth countries once → `data/cache/` (India clip + offline basemap)
3. Builds the site layer: `data/seed_sites.csv` + WRI Global Power Plant DB (+ OSM if present, see §5)
4. Samples ESA WorldCover land cover per 1 km cell (one small overview read per 3° tile) → `data/cache/landcover_cells.parquet`
5. Rolling persistence features, rules, persistent-source registry, anomalies, ML prediction
6. Writes `data/frims_live.gpkg` (layers `detections`, `sites`, `persistent_sources`)

The API loads every `data/frims_*.gpkg` at startup and refreshes `live` every 3 h in-process.

## 4. Historical dataset + validation (the numbers in the README)

Standard-processing (SP) VIIRS data carries the `type` attribute used as ground truth.

```bash
# ground truth pulls (cached forever once complete; ~37 calls / ≤1500 tx each)
uv run python scripts/build_truth.py --source VIIRS_NOAA20_SP --start 2025-08-01 --end 2026-01-31
uv run python scripts/build_truth.py --source VIIRS_NOAA20_SP --start 2026-02-01 --end 2026-05-31
uv run python scripts/build_truth.py --source VIIRS_SNPP_SP   --start 2025-08-01 --end 2026-01-31
uv run python scripts/build_truth.py --source VIIRS_SNPP_SP   --start 2026-02-01 --end 2026-05-31

# rules + ML vs truth (tunes/trains on Nov–Dec 2025, holds out Jan–May 2026) → models/eval_report.json, models/gbm.joblib
uv run python -m frims.classify.evaluate --eval-end 2026-05-31

# historical SP dataset (both sensors, Oct 2025 → May 2026; 0 network calls thanks to the cache) → data/frims_sp2025_26.gpkg
uv run python scripts/run_pipeline.py --dataset sp2025_26 --source-set sp --start 2025-10-15 --end 2026-05-31

# reported-incident check → models/incident_report.json (needs both datasets)
uv run python -m frims.classify.incidents
```

Re-run the live pipeline after `evaluate` so `label_ml` comes from the truth-trained model:
`uv run python scripts/run_pipeline.py --backfill-days 90 --offline`.

## 5. OSM industrial polygons (optional but recommended)

```bash
mkdir -p data/raw/osm
curl -L -o data/raw/osm/india-latest.osm.pbf https://download.geofabrik.de/asia/india-latest.osm.pbf   # ~1.7 GB
uv run python -c "from frims.ingest.osm_pbf import load_pbf_sites; print(len(load_pbf_sites(refresh=True)))"  # ~43k features, ~80 s
```

The pipeline picks up `data/cache/osm_sites.gpkg` automatically. Overpass is the fallback if no PBF exists
(`OVERPASS_URL` env var; public mirrors are frequently unreachable).

## 5b. Gas-flare catalogue (optional, recommended)

Sign in at https://daac.ornl.gov/cgi-bin/dsviewer.pl?ds_id=1874 (free Earthdata account) and download the
`eog_global_flare_survey_<year>_flare_list.csv` files anywhere under `data/raw/flares/`. The pipeline picks them
up automatically (355 flare sites inside India).

## 5c. Coal-mine positions (optional)

Request the *Global Coal Mine Tracker* spreadsheet at
https://globalenergymonitor.org/projects/global-coal-mine-tracker/download-data/ (free, CC BY 4.0) and drop the
`.xlsx` into `data/raw/gem/`; `ingest/gem.py` loads operating and mothballed mines inside India as `coal_mine` sites
sized by their reported area (394 in the Aug 2026 release).

## 6. Pipeline flags

| Flag | Meaning |
|---|---|
| `--backfill-days N` | live window length (default 90) |
| `--dataset NAME` | output `data/frims_NAME.gpkg` (default `live`) |
| `--source-set nrt\|sp` | NRT sensors vs SP sensors |
| `--start/--end` | explicit date range instead of "last N days" |
| `--dry-run` | print planned FIRMS calls and transaction estimate, no network |
| `--offline` | FIRMS from cache only |
| `--no-osm`, `--no-landcover`, `--no-clip`, `--no-train` | skip the respective step |

Environment variables: `MAP_KEY` (required), `FRIMS_BBOX` (default `68,6,98,36`), `FRIMS_DATA_DIR`,
`FRIMS_REFRESH_HOURS` (default 3, `0` disables the scheduler), `OVERPASS_URL`.

## 7. Tests

```bash
uv run pytest          # 17 tests: rules (incl. flare/mining/veto cases), features & join priority, sources, evaluation
```

## 8. Running without internet

Once data has been pulled, the whole system is offline-capable: Leaflet is vendored in `web/vendor/`, the datasets,
model and reports are on disk, and the map falls back automatically to an **offline vector basemap**
(`/api/basemap`, Natural Earth outlines) when tile servers are unreachable. The scheduled refresh just logs a failed
call and retries next cycle.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Invalid day range. Expects [1..5]` | FIRMS caps NRT area calls at 5 days (NASA's tutorial says 10 — outdated). Already handled by `FIRMS_MAX_DAYS_PER_CALL`. |
| `mapkey_status` near 5000 | The client sleeps until the 10-minute window resets. `--dry-run` shows the estimate first. |
| Overpass 5xx / timeouts | Use the Geofabrik PBF route (§5). |
| `GEOSException: side location conflict` | Invalid reprojected polygons — handled with `make_valid` in `boundary.py`. |
| WorldCover tile 404 | Sea tiles do not exist; those cells are marked `unknown`. |
| pandas `str` dtype surprises | pandas 3 uses Arrow-backed strings; labels are converted with `.to_numpy(dtype=object)` before sklearn. |
| Chrome screenshot freezes | Avoid `backdrop-filter` on the panel; keep `preferCanvas: true`. |

## 10. Rough budget

| Step | Time | FIRMS tx |
|---|---|---|
| Live 90-day pull (4 sensors) | 3–4 min | ~700 |
| SP truth pull (one sensor, 6 months) | 3–4 min | ≤ 1500 |
| Land cover for all of India (first time) | ~1 min | 0 |
| OSM PBF extraction | ~80 s | 0 |
| Live pipeline from cache | ~30 s | 0 |
| SP dataset (1.2 M detections) | ~3 min | 0 |
