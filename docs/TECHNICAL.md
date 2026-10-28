# FRIMS — Technical reference

Exact algorithms, formulas, schemas and constants as implemented (values read from the code and reports on
2026-08-29). Plain-language version: `HOW_IT_WORKS_PLAIN.md`. File map and history: `PROJECT_NOTES.md`.

---

## 0. Pipeline at a glance

```
run_pipeline.py
 ├─ 1 ingest      FirmsClient.fetch_all / fetch_range        → DataFrame (one row per detection)
 ├─ 2 clip        boundary.clip_to_india                      → rows inside India ∪ 200 km sea
 ├─ 3 sites       sites.build_sites (+ clip)                  → GeoDataFrame of industrial footprints
 ├─ 4 features    features.build.build_features
 │     ├─ add_grid                    0.01° cell id
 │     ├─ add_rolling_persistence     30/90-day per-detection stats
 │     ├─ join_sites                  curated-first nearest footprint
 │     ├─ add_site_persistence        90-day stats over the whole site
 │     └─ add_landcover               WorldCover class per cell
 ├─ 5 rules       classify.rules.classify_rules               → label_rule
 ├─ 6 sources     features.sources.build_sources + flag_anomalies → source_id, anomaly_score (may relabel to industrial_fire)
 ├─ 7 ml          classify.model.predict                      → label_ml, proba, proba_industrial
 └─ 8 store       storage.gpkg.write_*                        → data/frims_<dataset>.gpkg
api/app.py loads every data/frims_*.gpkg; web/ renders it.
```

---

## 1. Ingest — `ingest/firms.py`

### 1.1 Endpoint and window rules
`GET {FIRMS_BASE}/api/area/csv/{MAP_KEY}/{SOURCE}/{W,S,E,N}/{DAYS}[/{START_DATE}]`

- `DAYS ∈ [1, 5]` for NRT sources (server enforces; NASA's tutorial still says 10).
- Without `START_DATE` the window is "the last DAYS days"; with it, `[START, START+DAYS)`.
- Sources: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT` (source set `nrt`);
  `VIIRS_NOAA20_SP`, `VIIRS_SNPP_SP` (source set `sp`, carries `type`).
- Bbox default `68,6,98,36` (`FRIMS_BBOX`).

### 1.2 Transaction budget
`mapkey_status` → `{current_transactions, transaction_limit=5000, transaction_interval="10 minutes"}`.
Cost is proportional to rows returned (observed ≈ 1 tx / 700 rows, ≥ 1 per call).

```
budget_guard(estimate):
    if no status yet or calls_since_status ≥ 3: status()
    while used + estimate > limit − 500:  sleep 60 s; status()
estimate(source) = last observed (used_after − used_before) for that source, default 100
```
After each call the client re-reads `mapkey_status` and stores the observed cost per source.

### 1.3 Cache
Path: `data/raw/firms/<SOURCE>/<start>_<days>d_<bbox>.parquet`. A window is *complete* when its last day < today;
complete windows are never refetched. A window that ends today is refetched only if older than
`FIRMS_REFRESH_MINUTES = 30`.

`fetch_range(source, start, end)`:
1. list cached windows for the source/bbox; take those overlapping `[start, end]` (skip stale partial ones);
2. compute uncovered gaps; fetch each gap in ≤ 5-day windows (`fetch_window`);
3. concat, filter `start ≤ acq_date ≤ end`, drop duplicates on `(source, latitude, longitude, acq_date, acq_time)`.
Hence any range can be composed from windows of *any* alignment with zero network calls if covered.

### 1.4 Robustness
`_get_csv`: `timeout=(30 s connect, 600 s read)`; 4 attempts, backoff 5 → 10 → 20 → 40 s on network errors, HTTP 5xx
or 429; **immediate** `FirmsError` on other 4xx ("Invalid day range", "Invalid source") and on empty / "Invalid …"
bodies. A failed window is logged and skipped; the rest of the range proceeds.

### 1.5 Normalisation (`normalise`)
| Output | VIIRS | MODIS |
|---|---|---|
| `bt`, `bt2` | `bright_ti4`, `bright_ti5` (K) | `brightness`, `bright_t31` (K) |
| `conf_norm` | `l→0.3, n→0.6, h→0.9` | `confidence/100` |
| `acq_datetime` | `acq_date + HHMM` (UTC) | same |
| `is_night` | `daynight == "N"` | same |
| `sp_type` | `type` (SP only): 0 veg fire, 1 volcano, 2 static land source, 3 offshore | — |

---

## 2. Clip — `ingest/boundary.py`

Natural Earth 50 m admin-0 (cached GeoJSON). In EPSG:7755 (India-centric, metres):

```
india      = make_valid(India polygons).union_all()
neighbours = make_valid(countries intersecting india.buffer(2°)).union_all()
zone       = india.buffer(200 000 m).difference(neighbours).union(india)
```
`clip_to_india(df)`: point-in-polygon via `sjoin(..., predicate="within")` on the cached zone. Applied to detections
and to sites. Effect on the 90-day live pull: 103,754 → 68,268 rows (65.8 % kept).

---

## 3. Sites — `ingest/sites.py`, `osm_pbf.py`, `osm.py`

| Provider | Rows | Geometry | Footprint radius |
|---|---|---|---|
| seed (`data/seed_sites.csv`) | 53 | point | per row (1.5–40 km; e.g. Jamnagar 6 km, Jharia 15 km, Bombay High 40 km) |
| WRI Global Power Plant DB (thermal fuels in bbox) | 391 | point | `500 + 0.5 × min(capacity_MW, 5000)` m |
| OSM (Geofabrik `india-latest.osm.pbf`, pyosmium) | 43,188 | 40,869 multipolygons + 2,319 points | polygons as-is; points 300 m |
| GEM Global Coal Mine Tracker, Aug 2026 (`ingest/gem.py`) | 394 | point | √(mine area/π), clipped 1–8 km; 1.5 km if unknown |
| EOG Nightfire flare survey 2012–2019 (`ingest/flares.py`, ORNL DAAC 1874) | 355 | point | 1,000 m |

OSM tags → `site_type` (`classify_tags`): `industrial=refinery|oil|gas|petrochemical|steel|chemical`,
`man_made=flare|petroleum_well|works`, `power=plant` (+ `plant:source` thermal), `resource~coal`, `landuse=mine`,
`landuse=quarry`, else `industrial_area`. Extraction: `osmium.FileProcessor(pbf).with_areas().with_filter(
TagFilter(...))` then a second pass with `KeyFilter("industrial")`; ways/relations must remain in the entity mask
or no areas are assembled. 43,369 features in 82 s.

`site_footprints(sites)`: project to EPSG:7755, buffer points by `radius_m`, back to EPSG:4326.

---

## 4. Land cover — `ingest/landcover.py`

ESA WorldCover 2021 v200, 3°×3° COG tiles on AWS. Per tile, one read at overview factor 64
(`out_shape = (H/64, W/64)`, ≈ 640 m/pixel, `Resampling.nearest`); cell centres are mapped through the scaled
affine transform `inv(transform) · (lon, lat) → (col, row)`. Missing tiles (sea) → code 0 = `unknown`.
Cache: `data/cache/landcover_cells.parquet` (736,224 cells, 0 % unknown over the current datasets).
Classes: 10 tree_cover, 20 shrubland, 30 grassland, 40 cropland, 50 built_up, 60 bare_sparse, 70 snow_ice,
80 water, 90 wetland, 95 mangroves, 100 moss_lichen.

---

## 5. Features — `features/build.py`

### 5.1 Grid
`GRID_DEG = 0.01` (~1.1 km). `grid_id = f"{floor(lat/0.01)}_{floor(lon/0.01)}"`, cell centre = `(i+0.5)·0.01`.

### 5.2 Rolling persistence (`_rolling_window_stats`)
For every detection, statistics over detections of the same group *g* (grid cell or site) with
`acq_date ∈ (d − W, d]`, W ∈ {30, 90} days.

Implementation (fully vectorised):
1. `day = (acq_date − 2000-01-01).days`; `g = factorize(group)`.
2. Daily aggregates per (g, day): `n, night, frp_sum, frp_max, bt_sum`, sorted; key `k = g·10⁶ + day`.
3. Prefix sums `C[·]` over the sorted daily table.
4. Per row: `lo_day = max(d − W + 1, dmin)`, `hi_day = max(d, lo_day + W − 1)`  ← **warm-up**: while fewer than W
   days of history exist, the window extends forward so it always spans W days.
   `lo = searchsorted(k, g·10⁶ + lo_day, "left")`, `hi = searchsorted(k, g·10⁶ + hi_day, "right")`.
5. `n_days = hi − lo`; `n_det = C_n[hi] − C_n[lo]`; `night_frac = (C_night[hi] − C_night[lo]) / max(n_det, 1)`;
   `frp_mean`, `bt_mean` likewise; `frp_max = max(daily frp_max[lo:hi])`;
   `recurrence = n_days / clip(hi_day − max(first_day_g, lo_day) + 1, 1, W)`.

Outputs: `n_days_30, n_days_90, n_det_90, night_frac_90, frp_mean_90, frp_max_90, bt_mean_90, recurrence_90`.
Verified equal to the static per-cell computation on the newest day (max abs diff 0). 103 k rows in 0.1 s.

`frp_ratio = frp / frp_mean_90` (1.0 if undefined).

### 5.3 Site join (`join_sites`)
Provider-ordered stages of `sjoin_nearest(max_distance = SITE_MATCH_DISTANCE_M = 1500 m)` in EPSG:7755: seed →
WRI → GEM → Nightfire flares → OSM; a detection is claimed by the first provider whose footprint contains or is within
1.5 km of it (a 1 km flare point inside the Jamnagar complex must not steal the complex's persistence). Outputs `site_id, site_name, site_type,
dist_site_m` (50 000 if none), `near_site = matched`, `in_site = dist == 0`.

### 5.4 Site-level persistence (`add_site_persistence`)
Same rolling algorithm grouped by `site_id` → `site_days_90`; forced to 0 for
`generic_site_types = (industrial_area, works, quarry)`; `days_90_eff = max(n_days_90, site_days_90)`.

### 5.5 Feature vector for ML (`NUMERIC_FEATURES`, 21)
`bt, bt2, frp, conf_norm, is_night, scan, track, n_days_30, n_days_90, n_det_90, night_frac_90, frp_mean_90,
frp_max_90, bt_mean_90, recurrence_90, frp_ratio, in_site, dist_site_m, landcover_code, site_days_90, days_90_eff`.
`sp_type` is never a feature.

---

## 6. Rules — `classify/rules.py`

Constants (`config.RuleThresholds`): `persistent_min_days_90 = 10`, `flare_min_night_fraction = 0.5`,
`agri_max_days = 3`, `flare_site_types = (refinery, oil_gas, lng, petrochemical, offshore_platform)`,
`mining_site_types = (coal_mine, coalfield, mine, quarry)`, `generic_site_types = (industrial_area, works, quarry)`.

With `persistent := days_90_eff ≥ 10`, `specific := site_type ∉ generic`, `VEG = {tree_cover, shrubland,
grassland, wetland, mangroves}`; evaluated with `np.select`, **first true condition wins**:

| # | Condition | Label |
|---|---|---|
| 1 | `near_site ∧ site_type = volcano` | `volcano` |
| 1b | `near_site ∧ site_type = flare_site` (catalogued flare, any persistence) | `gas_flare` |
| 2 | `near_site ∧ site_type ∈ flare ∧ night_frac_90 ≥ 0.5 ∧ persistent` | `gas_flare` |
| 3 | `near_site ∧ site_type ∈ mining ∧ persistent ∧ n_days_90 ≥ 10` (cell itself recurring) | `mining_thermal` |
| 4 | `near_site ∧ persistent ∧ (in_site ∨ n_days_90 ≥ 5) ∧ ¬mining_fail` | `industrial_persistent` |
| 5 | `in_site ∧ specific ∧ ¬mining_fail` | `industrial_fire` |
| 6 | `n_days_90 ≥ 10` (cell-level; no vouching site) | `industrial_persistent` (unmapped source) |

`mining_fail := site_type ∈ mining ∧ n_days_90 < 10` — a 15 km coalfield radius must not vouch for a farm fire
inside it; such detections fall through to land cover. Tuned on Nov–Dec (rules F1 0.879 → 0.884), hold-out
0.866 → 0.882.
| 7 | `landcover = water` | `offshore_other` |
| 8 | `landcover = cropland ∧ day ∧ n_days_90 ≤ 3` | `agricultural_burn` |
| 9 | `landcover ∈ VEG` | `wildfire` |
| 10 | `landcover = cropland` | `agricultural_burn` |
| — | default | `offshore_other` |

`explain(row)` renders the evidence string shown in popups.

---

## 7. Persistent sources and anomalies — `features/sources.py`

**Registry** (`build_sources`): cells with `n_days_90 ≥ 10` in the last 90 days → DBSCAN in EPSG:7755,
`eps = 2000 m`, `min_samples = 1` → `source_id`. Per source: convex hull of member cell centres buffered 0.006°,
modal `label_rule`, modal `site_name/site_type/landcover`, `active_days_90`, `n_det_90`, `night_frac_90`,
`frp_mean_90`, `frp_std_90` (population σ), `frp_max_90`, `first_seen`, `last_seen`, centroid.

**Anomaly** (`flag_anomalies`), for a detection with a `source_id`:
```
z = (frp − frp_mean_90(source)) / max(frp_std_90(source), 1)
anomalous = (z ≥ 4 ∧ frp ≥ 10 MW ∧ frp ≥ 2·frp_mean_90)  ∨  (daytime ∧ night_frac_90(source) ≥ 0.9)
```
If anomalous and `label_rule ∈ {industrial_persistent, gas_flare, mining_thermal}` → relabel `industrial_fire`.
`anomaly_score = z` is stored for every source member. **Watch tier**: `¬anomalous ∧ z ≥ 2 ∧ frp ≥ 1.5·mean ∧
frp ≥ 5 MW` → `alert_tier = "watch"` (surfaced via `/api/alerts?tier=watch`, never relabelled).

`source_timeseries(feats, sid, days)`: daily `n, frp_max, frp_sum, night` for the drill-down sparkline.

---

## 8. ML — `classify/model.py`, `classify/evaluate.py`

**Truth**: SP `type` → `industrial` (2), `natural` (0), `other` (1, 3). Our fine labels →
`industrial = {industrial_persistent, gas_flare, mining_thermal}`, `natural = {agricultural_burn, wildfire,
industrial_fire}`, else `other`. (SP cannot distinguish a one-off industrial fire from any transient fire.)

**Model**: `HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
class_weight="balanced", random_state=0)` on the 21 features, binary `industrial/natural`.

**Split**: train = truth rows with `2025-11-01 ≤ acq_date ≤ 2025-12-31` (268,478 rows); test = `2026-01-01 →
2026-05-31` (1,494,392 rows). Aug–Oct 2025 exists only to warm up the 90-day windows.

**Results** (`models/eval_report.json`):

| Evaluation | n | acc | industrial P / R / F1 | natural P / R / F1 |
|---|---|---|---|---|
| Rules, tuning period Nov–Dec 2025 | 269,791 | 0.932 | 0.818 / 0.962 / 0.884 | 0.993 / 0.929 / 0.960 |
| Rules, hold-out Jan–May 2026 | 1,497,284 | 0.973 | 0.798 / 0.986 / **0.882** | 0.999 / 0.974 / 0.986 |
| ML, hold-out Jan–May 2026 | 1,494,392 | 0.982 | 0.787 / 0.977 / 0.872 | 0.999 / 0.983 / 0.990 |
| **Combined (rules + ML veto p < 0.3), hold-out** | 1,497,284 | 0.973 | 0.812 / 0.979 / **0.887** | — |

Hold-out confusion (rows = truth, cols = predicted; industrial, natural, other):
rules `[[91278, 773, 257], [24182, 1362216, 15686], [1977, 492, 423]]`;
ML `[[90240, 2068, 0], [24373, 1377711, 0]]`.

Permutation importance (accuracy drop, test set): `n_days_90 0.038`, `night_frac_90 0.016`,
`days_90_eff 0.011`, `frp_mean_90 0.007`, `dist_site_m 0.002`, everything else < 0.001 — persistence and night
fraction carry the signal; instantaneous brightness/FRP alone carry none.

Fine-class composition of truth on the tuning period (`fine_class_vs_sp`): e.g. `gas_flare` 1593 static / 181
veg; `mining_thermal` 18587 / 4732; `industrial_persistent` 25272 / 4970; `agricultural_burn` 482 / 177046;
`wildfire` 604 / 28353. The 4–5 k "natural" rows inside the persistent classes are the precision cost.

**Inference** (`predict`): `label_ml = argmax`, `proba = max class prob`, `proba_industrial`. Fallback when no
truth-trained model exists: `train_on_rules` (logged as *not a validation*).

**Combined label** (`_combine`): `label_final = label_rule`, except when `label_rule ∈ {industrial_persistent,
gas_flare, mining_thermal}` and `proba_industrial < ML_VETO_P = 0.3` → `fallback_by_landcover` (cropland →
agricultural_burn, vegetation → wildfire, else offshore_other); `ml_veto = True`. `industrial_fire`, `volcano` and detections at catalogued
`flare_site`s are never vetoed; no veto when the model was trained on rules. Hold-out confusion (industrial, natural, other):
`[[90445, 1410, 453], [19207, 1366279, 16598], [1834, 506, 552]]`. Threshold sweep on the hold-out: F1 0.887–0.889 for p ∈ [0.2, 0.5]; raising the persistence threshold
instead gave no gain (F1 flat, recall −1.5 to −5 pts).

**Truth noise**: among cells with ≥ 30 active days / 90 and night fraction ≥ 0.9, NASA labels only 72 % of detections
static on average and 24 % of such cells are majority "vegetation"; 27 % of hold-out false positives fall in cells NASA
sometimes labels static → practical ceiling ≈ 0.92.

---

## 9. Incident check — `classify/incidents.py`

Input `data/incidents.csv` (`id, name, date, days_after, lat, lon, radius_km, type, expected_visible,
geocode_note, source_url`). For each incident: dataset whose date range covers `date`; detections within
`radius_km` (equirectangular distance) and `date − 1 d ≤ acq_date ≤ date + days_after`; report
`n_detections, n_days, frp_max, labels, ml_industrial_share, alert_fired (= any industrial_fire ∨ anomaly ≥ 4),
n_alerts, nearest_site, anomaly_z_max, industrial_share, baseline_30d_{days,detections}, first_seen, sp_types,
location_confidence (high if radius ≤ 10 km)`. Summary: 14 incidents, 8 detected, 1 alerted; expected-visible
3 → 2 detected, 1 alerted.

---

## 10. Storage — `storage/gpkg.py`

`data/frims_<dataset>.gpkg`, EPSG:4326:

- `detections` — `source, satellite, instrument, latitude, longitude, bt, bt2, frp, acq_date, acq_datetime
  (ISO strings), daynight, confidence, conf_norm, sp_type (−1 if none), grid_id, n_days_30, n_days_90, n_det_90,
  night_frac_90, frp_mean_90, frp_max_90, recurrence_90, frp_ratio, site_type, site_name, dist_site_m, in_site,
  near_site (0/1), site_days_90, days_90_eff, landcover, landcover_code, label_rule, label_ml, proba,
  proba_industrial, source_id (−1 if none), anomaly_score, explanation, geometry(Point)`
- `sites` — `name, site_type, latitude, longitude, radius_m, provider, notes, site_id, geometry`
- `persistent_sources` — the registry columns of §7, `geometry(Polygon)`

Current: `live` 65,403 detections / 195 sources; `sp2025_26` 1,180,747 / 409 sources; 44,381 sites.

---

## 11. API — `api/app.py`

All data endpoints accept `dataset` (default `live`). Window = `(end − days, end]` with `end` defaulting to the
dataset's latest date.

| Route | Params | Returns |
|---|---|---|
| `GET /api/health` | — | per-dataset `n, min/max date, latest_detection_utc, data_age_hours`; refresh state |
| `GET /api/datasets` | — | list |
| `GET /api/detections` | `days≤400, end, classes, sources, bbox, label∈{label_rule,label_ml}, min_conf, limit≤150000` | GeoJSON (random sample beyond `limit`) |
| `GET /api/sites` | `types` | GeoJSON |
| `GET /api/sources` | `label` | GeoJSON polygons |
| `GET /api/sources/{id}/timeseries` | `days` | meta + daily series |
| `GET /api/stats` | `days, end, label` | totals, by_class, by_source, daily matrix, industrial_share, anomalies |
| `GET /api/alerts` | `days, end, tier∈{alert,watch,all}` | `industrial_fire` rows (or watch-tier rows) sorted by `anomaly_score, frp` |
| `GET /api/eval` | — | model info + `eval_report.json` |
| `GET /api/incidents` | `live` | `incident_report.json` (or recomputed) |
| `GET /api/basemap` | — | Natural Earth outlines near the bbox (offline basemap) |
| `POST /api/refresh` | — | starts pipeline in background; debounced `REFRESH_DEBOUNCE_MINUTES = 15` |

Scheduler: daemon thread runs the pipeline every `FRIMS_REFRESH_HOURS = 3` (0 disables) and reloads datasets.
Static files: `web/` mounted at `/` (Leaflet vendored).

---

## 12. Configuration constants (`config.py`)

| Name | Value |
|---|---|
| `BBOX` | `68, 6, 98, 36` |
| `TX_LIMIT_DEFAULT / TX_SAFETY_MARGIN / TX_DEFAULT_ESTIMATE` | 5000 / 500 / 100 |
| `TX_STATUS_EVERY_N_CALLS` | 3 |
| `FIRMS_MAX_DAYS_PER_CALL` | 5 |
| `FIRMS_SLEEP_BETWEEN_CALLS_S` | 2.0 |
| `FIRMS_REFRESH_MINUTES` | 30 |
| `GRID_DEG` | 0.01 |
| `PERSISTENCE_WINDOWS_DAYS` | (30, 90) |
| `SITE_MATCH_DISTANCE_M` | 1500 |
| `VOLCANO_MATCH_M` | 6000 |
| `OFFSHORE_BUFFER_M` (boundary.py) | 200 000 |
| `CLUSTER_EPS_M / ANOMALY_SIGMA / ANOMALY_MIN_FRP_MW / ANOMALY_MIN_RATIO` (sources.py) | 2000 / 4 / 10 / 2 |
| `OVERVIEW_FACTOR` (landcover.py) | 64 |
| `mining_min_cell_days_90 / near_site_min_cell_days_90` | 10 / 5 |
| `ML_VETO_P` | 0.3 |
| `WATCH_SIGMA / WATCH_MIN_RATIO` (sources.py) | 2 / 1.5 |
| `REFRESH_EVERY_HOURS` | 3 |

---

## 13. Complexity and measured performance

| Stage | Complexity | Measured |
|---|---|---|
| rolling stats | O(n log n) (sort + searchsorted) | 103 k rows: 0.1 s |
| site join | O(n log m) per provider (STRtree) | 1.18 M × 43 k: seconds |
| DBSCAN on persistent cells | O(c log c), c ≈ 400–1000 cells | < 1 s |
| GBM predict | O(n · trees) | 1.18 M rows: ~5 s |
| GeoPackage write | O(n) | 1.18 M rows: ~15 s |
| whole pipeline | — | 65 k: 4 s; 1.18 M: 34 s (≈ 35 k det/s) |
| API | — | 300 req/s small JSON; 26 req/s for 780 KB GeoJSON (10 concurrent) |
