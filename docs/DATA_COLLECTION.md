# FRIMS — Data collection and efficiency

What data we use, exactly how it is collected, how much of it there is, and the efficiency figures we measured
(all numbers as of 2026-08-29; see `TECHNICAL.md` for the algorithms).

---

## 1. Sources

| # | Data | Provider / access | Format | What we take | Volume on disk |
|---|---|---|---|---|---|
| 1 | Active-fire detections, near-real-time (NRT) | NASA FIRMS `api/area/csv` — VIIRS S-NPP, NOAA-20, NOAA-21 (375 m), MODIS Aqua/Terra (1 km) | CSV per call | India bbox, rolling 90 days, 5-day windows | 76 windows, 103,832 rows, 4.2 MB |
| 2 | Active-fire detections, standard processing (SP) with `type` | NASA FIRMS — VIIRS NOAA-20 SP, S-NPP SP | CSV | Aug 2025 → May 2026, ground truth + historical dataset | 156 windows, 2,815,404 rows, 79.5 MB |
| 3 | Land cover | ESA WorldCover 2021 v200, public COGs on AWS (`esa-worldcover` bucket) | GeoTIFF COG, 3°×3° tiles | one ~640 m overview read per tile; class per 1 km cell | 736,224 cells cached (parquet, ~6 MB) |
| 4 | Industrial infrastructure — curated | `data/seed_sites.csv` (hand-built) | CSV | 53 refineries, coalfields, steel, LNG, offshore fields, power, volcanoes, ONGC fields | 5 KB |
| 5 | Industrial infrastructure — power plants | WRI Global Power Plant Database (GitHub CSV) | CSV | thermal-fuel plants inside bbox → 391 after clip | 20 KB |
| 6 | Industrial infrastructure — OSM | Geofabrik `asia/india-latest.osm.pbf` | PBF | `landuse=industrial|quarry|mine`, `man_made=works|flare|petroleum_well|oil_refinery`, `power=plant`, `industrial=*`, `resource=coal` → 43,188 features | 1.7 GB raw; 43k features cached in a 25 MB GeoPackage |
| 6b | Industrial infrastructure — gas flares | EOG / NASA ORNL DAAC *Global Gas Flare Survey by Infrared Imaging, VIIRS Nightfire 2012–2019* (DOI 10.3334/ORNLDAAC/1874; open licence, Earthdata sign-in) | CSV per year | 533 unique flare sites in bbox → 355 inside the India zone, typed upstream/LNG, 1 km footprint | 8 × ~1 MB |
| 6c | Industrial infrastructure — coal mines | Global Energy Monitor *Global Coal Mine Tracker* (Aug 2026 release; CC BY 4.0, spreadsheet via download form) | XLSX | 552 India rows → 394 operating/mothballed mines inside the India zone; footprint = equivalent-circle radius of reported mine area (1–8 km) | 3 MB |
| 7 | Country boundaries | Natural Earth 50 m admin-0 (GitHub GeoJSON) | GeoJSON | India zone (+200 km sea, minus neighbours); offline basemap | 5 MB |
| 8 | Reported incidents | News/agency reports, geocoded with Nominatim | CSV (`data/incidents.csv`) | 14 dated, source-linked events | 4 KB |

Licences: FIRMS (NASA, free with MAP_KEY), WorldCover (CC BY 4.0), WRI GPPD (CC BY 4.0), OSM (ODbL), Natural Earth
(public domain).

---

## 2. How each source is collected

### 2.1 FIRMS (sources 1–2)
- One HTTPS call per (sensor, 5-day window, bbox). Every completed window is cached as parquet and **never
  refetched**; any date range is composed from cached windows of any alignment and only the gaps are fetched.
- A budget guard reads `mapkey_status` before batches and after every call, learns the real cost per sensor, and
  sleeps when `used + estimate > 4500`. `--dry-run` prints the planned calls and estimated cost before any network
  traffic.
- Windows ending today are refreshed at most every 30 minutes; the API's scheduler refreshes `live` every 3 hours.
- Responses are normalised to one schema (VIIRS `bright_ti4` and MODIS `brightness` → `bt`, letter/number
  confidence → `conf_norm`, `type` → `sp_type`).

### 2.2 WorldCover (source 3)
Instead of point-sampling the 10 m rasters over HTTP (one block read per point; hung for > 10 min on 40 k points),
each 3° tile is opened once with rasterio (`/vsicurl/`) and read at overview factor 64 (~1 s, a few hundred KB);
every 1 km cell inside the tile is looked up in memory. Cells are cached, so repeat runs read nothing.

### 2.3 Sites (sources 4–6)
Seed CSV and WRI CSV are read directly (WRI cached after the first download). The OSM extract is streamed with
pyosmium (`FileProcessor` + `TagFilter`/`KeyFilter`, `with_areas()` assembling multipolygons) in 82 s and cached as
`data/cache/osm_sites.gpkg`. Overpass API remains a fallback but was unusable from our network (timeouts, 5xx).
All providers are merged into one layer; points get a footprint radius (seed: per row; WRI: `500 + 0.5·MW` m;
OSM points: 300 m), OSM polygons keep their real shape.

Coal mines (source 6c) are loaded by `ingest/gem.py` from any `.xlsx/.csv` in `data/raw/gem/` (operating + mothballed,
columns detected by name) and rank above the Nightfire and OSM layers in the join. Effect: measured score unchanged
(areas already covered), but 3,551 live detections at 90 named mines gain attribution and mining labels rise ~450 in
basins the seed list missed.
Flare sites (source 6b) are loaded by `ingest/flares.py` from any `*flare_list*.csv` under `data/raw/flares/`,
de-duplicated across survey years (~500 m), and take priority over OSM in the site join. Effect: static detections
typed as `gas_flare` rose from 1,593 to 4,159 on the tuning period and spurious alerts at flares fell 261 → 19 per
90 days; measured hold-out F1 dips ≈ 0.4 pt because NASA's truth calls intermittent flaring "vegetation fire".

### 2.4 Boundary (source 7)
Downloaded once; the India zone polygon is computed in metres (EPSG:7755) with `make_valid` and cached.

### 2.5 Incidents (source 8)
Each row cites its source URL; coordinates from Nominatim with a `geocode_note`; `radius_km` encodes location
uncertainty (≤ 10 km = high confidence). One coordinate (ONGC Mori-5) was corrected after the first geocode hit a
different village 25 km away; that correction and the later addition of the ONGC Mori field to the seed sites are
recorded in the files.

---

## 3. Volumes collected

| Dataset | Period | Sensors | Detections | After clip | Persistent sources |
|---|---|---|---|---|---|
| `live` | last 90 days (rolling) | 3 × VIIRS NRT + MODIS NRT | ~103 k raw | 65,403 | 195 |
| `sp2025_26` | 2025-10-15 → 2026-05-31 | VIIRS NOAA-20 SP + S-NPP SP | 1,802,483 raw | 1,180,747 | 409 |
| truth set | 2025-08-01 → 2026-05-31 | same SP sensors | 2,815,404 | (not clipped; all used) | — |

Site layer: 44,381 features (43,188 OSM, 394 GEM coal mines, 391 WRI, 355 Nightfire flares, 53 seed). Land cover: 736,224 cells.

---

## 4. Efficiency figures

### 4.1 Collection efficiency (FIRMS)

| Metric | Value | How measured |
|---|---|---|
| Rows per transaction | **~1,300 rows / tx** overall (≈ 700 for sparse monsoon windows, > 2,000 for fire-season SP windows) | 2.92 M rows for ≈ 2,225 tx across all pulls |
| Peak budget use in any 10-minute window | **720 / 5000 = 14.4 %** | `mapkey_status` after the 90-day live backfill |
| Total budget consumed for the whole project | ≈ 2,225 tx ≈ **0.45 windows' worth** | sum of per-pull status readings (720 + 665 + 375 + 90 + 375) |
| Calls that failed permanently | 0 of 232 windows | logs |
| Cache hit rate after the initial pull | **100 %** — every subsequent pipeline run, evaluation and dataset build made 0 network calls (e.g. `sp2025_26` needed 0 of its 48 windows) | `--dry-run` planned calls = 0 |
| Sustained ingest rate | 656 rows/s (3.8 s per call incl. a 2 s pause) | 130 timed calls |
| Refresh cost | ~40 tx / 3 h → ~320 tx/day (6.4 % of one window) | 4 partial windows per refresh |

### 4.2 Collection efficiency (other sources)

| Source | Naïve approach | What we do | Saving |
|---|---|---|---|
| WorldCover | 736,224 HTTP point reads (hung) | ~30 tile overview reads | **> 99.99 % fewer requests**; ~1 min total, then 0 |
| OSM | Overpass tiled queries (failed, 5xx) | one PBF download + 82 s local extraction | deterministic; repeatable offline |
| Boundary / WRI | re-download each run | cached once | 0 network after first run |
| Clip | process all bbox rows | drop 34.2 % outside India up front | 34 % less feature work |

### 4.3 Coverage and agreement

| Metric | live | sp2025_26 |
|---|---|---|
| Detections within 1.5 km of a known site | 38.9 % | 17.7 % |
| Detections inside a persistent source | 31.0 % | 14.6 % |
| Land-cover class known | 100 % | 100 % |
| Rules vs ML agree (static-industrial vs not) | 94.9 % | 98.6 % |

### 4.4 Classification efficiency (vs NASA SP `type`, hold-out Jan–May 2026, 1.49 M detections)

| | Precision | Recall | F1 | Accuracy |
|---|---|---|---|---|
| Rules — static industrial source | 79.8 % | **98.6 %** | 88.2 % | 97.3 % |
| Rules — transient fire | 99.9 % | 97.2 % | 98.5 % | |
| ML — static industrial source | 78.7 % | 97.7 % | 87.2 % | 98.2 % |
| ML — transient fire | 99.9 % | 98.3 % | 99.1 % | |
| **Combined (rules + ML veto) — static industrial source** | 81.2 % | 97.9 % | **88.7 %** | 97.3 % |

Reading: we find ~98 % of everything NASA later confirms as a persistent industrial source, at the price of
~19 % of our "industrial" calls being transient fires near industrial land (precision) — of which about a quarter
sit in cells NASA itself labels inconsistently (likely kilns/small industry), so the practical ceiling is ≈ 0.92. On the tuning months
(Nov–Dec 2025) rules reached F1 88.2 %; the spring fire season lowers precision.

### 4.5 Event-level efficiency (14 real reported incidents)

| Metric | Value |
|---|---|
| Seen by satellite (≥ 1 detection in window and radius) | **8 / 14 = 57 %** |
| Alert raised | 1 / 14 (ONGC Mori-5 blowout, 5-day fire) |
| Watch tier (z ≥ 2, below alert grade) | 1 more (ONGC Mumbai High platform fire, z = 2.1) |
| Of 3 incidents expected to be visible | 2 seen (67 %), 1 alerted |
| Not detected | 6 — short daytime fires, indoor explosions, a cylinder blast |

### 4.6 Processing efficiency

| Stage | Throughput |
|---|---|
| Rolling persistence | 103 k detections in 0.1 s |
| Full pipeline (features → rules → sources → ML → GeoPackage) | 16 k det/s (65 k rows), **35 k det/s** (1.18 M rows) |
| OSM extraction | 43,369 features in 82 s |
| API | 300 req/s small JSON, 26 req/s for 780 KB GeoJSON (10 concurrent) |

---

## 5. Known gaps in the data

- **Latency**: FIRMS NRT is ~3 h behind the satellite; geostationary MSG-IODC is not exposed by the FIRMS API.
- **Site database**: new plants (e.g. Vedanta Sakti) and untagged GIDC estates are missing → detections there fall
  to land-cover rules. This is the main lever for alert recall.
- **SP truth semantics**: `type` marks static sources vs vegetation fires; it cannot validate one-off industrial
  fires, hence the separate incident set.
- **Sensor physics**: 375 m pixels, ~4 passes/day — six of the 14 incidents were physically invisible.
- **Neighbouring countries**: clipped out by design; the bbox still costs transactions for them (~34 % of rows).
