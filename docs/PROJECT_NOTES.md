# FRIMS — Project Notes: what we built, how, and what each file does

Companion to `../README.md` (overview + results), `SETUP.md` (how to run) and `PRODUCTION.md` (throughput and
global-scale architecture). This is the engineering record.

## 1. The problem (SIH 26162, NTRO)

NASA FIRMS gives thermal-anomaly detections but cannot tell a refinery flare or a burning coalfield from a wildfire
or stubble burning. Deliverables: (i) segregate industrial fires / persistent thermal sources from natural fires,
(ii) a GIS solution that stores results and shows them as a map overlay.

## 2. What we built

```
FIRMS API (VIIRS ×3, MODIS; NRT + SP) ─┐
Seed sites + WRI power plants + OSM PBF ─┼─► ingest ─► clip to India ─► features ─► rules ─► sources/anomalies ─► ML ─► GeoPackage
ESA WorldCover land cover (COG overviews)┘                                                                               │
                                                                     FastAPI (/api/*, GeoJSON) + vendored Leaflet UI ◄────┘
```

Seven+1 classes: `industrial_persistent`, `gas_flare`, `mining_thermal`, `industrial_fire` (alert),
`agricultural_burn`, `wildfire`, `volcano`, `offshore_other`; plus an independent ML label
(`industrial` static source vs `natural` transient) with a probability.

### Timeline

| Phase | Date | Outcome |
|---|---|---|
| 1 — MVP | 2026-08-28 | Ingest with rate-limit guard, sites, land cover, grid persistence, rules + GBM, GeoPackage, API, Leaflet UI. 103 754 detections / 90 d for 720 tx. |
| 2 — validation & completeness | 2026-08-28/29 | SP `type` ground truth, truth-trained ML with time hold-out, OSM via Geofabrik + pyosmium, India clip, DBSCAN source registry + anomaly alerts, historical dataset, UI (dataset switch, playback, sparkline, model tab). |
| 2b — event truth & ops | 2026-08-29 | Both VIIRS sensors as truth to May 2026; 14 verified real incidents checked; volcano class; in-process refresh scheduler + data age; cache-aware range fetch; offline mode (vendored Leaflet, vector basemap). |
| 2c — accuracy squeeze | 2026-08-29 (evening) | ML veto → `label_final`; watch tier; EOG Nightfire flare catalogue (355) with `flare_site` rule; GEM coal-mine tracker (394); provider-ordered join; cell-level persistence for mining/near-site; truth-noise ceiling estimate; docs + jury kit. |

### Results (see README for the full tables)

- Static-source vs transient, **Jan–May 2026 hold-out (1.49 M labelled detections)**: rules F1 0.882, ML 0.872,
  **combined (rules + ML veto) 0.887** — precision 0.811, recall 0.979. Started the day at 0.870; gains came from the
  ML veto (+1.8) and cell-level persistence for mining (+1.6 rules), minus 0.4 for typing catalogued flares correctly.
  NASA's truth is itself inconsistent in ~a quarter of our residual errors (ceiling ≈ 0.92).
- **Incidents**: 14 real events → 8 seen by satellite, 1 alert (ONGC Mori-5 blowout, 5 days). Events inside
  already-hot complexes are seen but not separable at 4σ; short/indoor events are invisible to 375 m sensors.
- Known sites behave: Jamnagar 99/101 `gas_flare`, Jharia 2,317/2,424 `mining_thermal` (the rest are one-off
  vegetation fires inside its 15 km circle), Hazira 771/779 `gas_flare`, Assam oil fields 144 flares.

## 3. How it works, component by component

**Ingest (`ingest/firms.py`).** `FirmsClient` wraps the FIRMS `area/csv` endpoint. Cost is per row (~1 tx / 700
rows) inside a 5000 tx / 10 min budget, so it (a) checks `mapkey_status` before batches and learns the real cost per
source, (b) sleeps when the budget is nearly spent, (c) caches every completed 5-day window to parquet and never
refetches it, (d) composes any requested range from cached windows regardless of alignment and fetches only gaps,
(e) fails fast on 4xx and retries with backoff on network errors. NRT calls are capped at 5 days by the API.
`normalise()` unifies VIIRS/MODIS schemas (`bt`, `conf_norm`, `is_night`, `acq_datetime`, `sp_type`).

**Sites (`ingest/sites.py`, `osm_pbf.py`, `osm.py`).** Three providers merged into one layer with `site_type`:
53 curated seed sites (refineries, coalfields, steel, LNG, offshore fields, volcanoes), 391 WRI thermal power plants,
394 GEM coal mines (`ingest/gem.py`), 355 EOG Nightfire flare sites (`ingest/flares.py`), 43 k OSM features (industrial landuse, quarries, works, flares, wells, power plants) extracted from the Geofabrik PBF
with pyosmium. Points are buffered by `radius_m`; OSM polygons keep their real footprint (`site_footprints`).

**Land cover (`ingest/landcover.py`).** ESA WorldCover 2021 10 m COGs on AWS. Instead of point-sampling over HTTP
(thousands of scattered block reads — it hung for >10 min), each 3° tile is read once at its ~640 m overview
(~1 s) and cells are looked up in memory; results cached per grid cell.

**Boundary (`ingest/boundary.py`).** Natural Earth 50 m admin-0: India ∪ 200 km sea buffer, minus neighbours' land
(so Bombay High stays, Karachi goes). Geometries are `make_valid`-ed before union.

**Features (`features/build.py`).** 0.01° grid cells; **rolling** per-detection persistence over the 30/90 days
preceding it (vectorised with sorted keys + cumulative sums: `n_days`, `n_det`, night fraction, FRP mean/max,
recurrence). During the first 90 days of a dataset the window extends forward (persistence describes the location).
Site join: curated sites first, OSM only for unmatched detections; site-level persistence (`site_days_90`) so a
refinery complex spanning many cells is one persistent thing — except for generic OSM areas, which never confer
persistence (tuned on truth).

**Rules (`classify/rules.py`).** Ordered, first match wins: volcano → flare (flare-type site, night ≥ 0.5,
persistent) → mining (mining site, persistent) → industrial_persistent (site, persistent) → industrial_fire (inside a
*specific* site, not persistent) → unmapped persistent source → water → cropland/day/≤3 days → vegetation →
cropland → other. Thresholds live in `config.RuleThresholds`.

**Sources & anomalies (`features/sources.py`).** DBSCAN (2 km) over persistent cells → sources with footprint,
label, linked site, first/last seen, FRP mean/σ. A detection inside a source with FRP ≥ mean + 4σ (and ≥ 2× mean,
≥ 10 MW), or a daytime detection at a night-only flare, becomes an `industrial_fire` alert with `anomaly_score`.

**ML (`classify/model.py`, `classify/evaluate.py`).** Ground truth = SP `type` (2 static land source → industrial,
0 vegetation fire → natural; 1/3 → other). SP cannot see one-off industrial fires (they are type 0), so
`industrial_fire` is evaluated as transient. `HistGradientBoosting` trained on Nov–Dec 2025 features (never on
`type`), evaluated on the following months; permutation importance recorded. `evaluate.py` also produces the
per-fine-class composition used to tune the rules.

**Incidents (`classify/incidents.py`, `data/incidents.csv`).** For each source-linked real incident: detections in
[−1 d, +N d] within `radius_km`, labels, alert, max anomaly z, nearest site, 30-day baseline, location confidence.

**Storage (`storage/gpkg.py`).** One GeoPackage per dataset with `detections`, `sites`, `persistent_sources` —
opens directly in QGIS (deliverable ii).

**API (`api/app.py`).** Loads every `data/frims_*.gpkg`; GeoJSON endpoints with dataset/day/end/class/sensor/bbox
filters; sources + time series; stats; alerts; eval and incident reports; offline basemap; debounced `POST
/api/refresh`; background scheduler thread; `health` with data age.

**UI (`web/`).** Vanilla Leaflet (vendored, canvas renderer): dataset dropdown, 24H/7D/30D/90D + window end date,
daily playback, class/sensor toggles (sensor list built per dataset), site footprints, persistent-source rings with
drill-down sparkline, alerts list, incident table, model/confusion-matrix tab, offline vector basemap fallback.

## 4. Decisions and lessons (the non-obvious ones)

1. **Rate limit is a design input, not a footnote** — the first plan glossed over it and was rejected; the client
   now budgets, caches and dry-runs.
2. **FIRMS NRT caps calls at 5 days**, not the 10 in NASA's tutorial; and SP responses in fire season are tens of MB
   (600 s read timeout).
3. **Static vs rolling persistence.** Static stats leaked the future into old detections; rolling stats made the
   first two weeks of every dataset look like "new fires" at Jamnagar — fixed with the forward warm-up window.
4. **OSM is double-edged.** 25 k generic `landuse=industrial` polygons dropped industrial precision 0.80 → 0.69 until
   generic areas stopped conferring persistence/alerts and curated sites took join priority.
5. **SP `type` semantics** define what can be validated: persistent-source classification yes, one-off industrial
   fires no — hence the separate incident check.
6. **pyosmium**: ways/relations must stay in `FileProcessor`'s entity mask or `with_areas()` yields no polygons.
7. **Overpass** public mirrors were unusable from this network (timeouts, 5xx) — Geofabrik + pyosmium is
   deterministic and 80 s.
8. **Chrome capture** hung with `backdrop-filter: blur` on the panel; removed. Canvas renderer for 60 k markers.
9. **MSG-IODC geostationary is not in the FIRMS API** (only GOES), so 10-minute cadence over India is not available
   this way.
10. **An ML veto beats threshold tuning.** Raising persistence thresholds gave nothing; letting the truth-trained
    model veto rule-industrial labels at p < 0.3 lifted hold-out F1 0.870 → 0.888 with a flat response across 0.2–0.5.
    A quarter of the remaining false positives sit in cells NASA labels inconsistently (ceiling ≈ 0.92).
11. **A flare catalogue can lower the measured score while improving the product**: NASA labels intermittent
    flaring as vegetation fire, so `flare_site → gas_flare` costs ≈ 0.4 pt F1 but removes ~240 spurious alerts.
    Also: join priority must be provider-ordered (seed → WRI → Nightfire → OSM) or a 1 km flare point inside a
    refinery complex steals its persistence (Jamnagar dropped to 60 % before the fix).
12. **A big footprint must not vouch for a non-recurring cell.** Coalfield radii (15 km) were labelling farm fires
    inside them as mining; requiring the detection's own cell to recur (≥ 10 d for mining, ≥ 5 d near-but-outside
    other sites) took hold-out rules F1 0.866 → 0.882 with tuning-month gains too, and alerts 422 → 373 per 90 d.
13. **Catalogues that overlap existing coverage change attribution, not the score.** GEM's 394 mines left hold-out
    F1 untouched (0.882 / 0.887) yet named 3,551 detections and added ~450 mining labels in uncovered basins —
    worth having for the operator, not for the metric.
14. **Empty FIRMS windows come back with object dtypes**; concat then poisons numeric columns — coerce after concat.
12. **Site-database completeness is the limiting factor** for alerts (Mori-5 was `wildfire` until the ONGC Mori
    field was added; Vedanta's new plant and Jhagadia GIDC are still unmatched).

## 5. File map

```
.
├── README.md                     overview, classification table, validation + incident results
├── docs/
│   ├── SETUP.md                  install / run / troubleshoot
│   ├── PROJECT_NOTES.md          this file
│   ├── PRODUCTION.md             measured throughput + production / global-scale architecture
│   ├── HOW_IT_WORKS_PLAIN.md     pipeline, rules and ML explained in plain language
│   ├── TECHNICAL.md              exact algorithms, formulas, schemas, constants, API contract
│   ├── DATA_COLLECTION.md        data sources, collection mechanics, volumes, efficiency figures
│   └── presentation/             jury deck (HTML, Marp), demo script
├── pyproject.toml, uv.lock       dependencies (uv); .python-version = 3.12
├── .env                          MAP_KEY (git-ignored)
├── ps.md                         SIH problem statement
├── Fire_data_SIH.txt             FIRMS layer/attribute reference (column semantics, confidence, `type`)
├── firms_api_use.ipynb           NASA FIRMS API tutorial (URL patterns, mapkey_status)
├── us_cannada_fire_map_ref.png   FIRMS UI screenshot used as the visual reference
│
├── src/frims/
│   ├── config.py                 paths, bbox, sources & source sets, rate-limit constants, RuleThresholds, classes,
│   │                             WorldCover URL/classes, Overpass/WRI URLs, refresh interval
│   ├── ingest/
│   │   ├── firms.py              FirmsClient (budget guard, cache, cache-aware fetch_range, backoff), normalise()
│   │   ├── sites.py              seed CSV + WRI power plants (+OSM) → sites layer; site_footprints()
│   │   ├── osm_pbf.py            Geofabrik PBF → industrial features via pyosmium (TagFilter/KeyFilter, areas)
│   │   ├── osm.py                Overpass fallback (tiled queries) + classify_tags() shared with osm_pbf
│   │   ├── flares.py             EOG/ORNL VIIRS Nightfire flare-survey loader (drop CSVs in data/raw/flares/)
│   │   ├── gem.py                Global Energy Monitor coal-mine tracker loader (drop xlsx/csv in data/raw/gem/)
│   │   ├── landcover.py          WorldCover overview reads per tile, per-cell cache
│   │   └── boundary.py           India zone (NE countries, 200 km offshore, neighbours excluded), clip_to_india()
│   ├── features/
│   │   ├── build.py              grid, rolling persistence (+warm-up), site join with priority, site persistence,
│   │   │                         land cover merge, NUMERIC_FEATURES
│   │   └── sources.py            DBSCAN persistent-source registry, anomaly flags, source_timeseries()
│   ├── classify/
│   │   ├── rules.py              ordered rule classifier, explain()
│   │   ├── model.py              truth-trained GBM (time split), rule-mimic fallback, predict(), model_info()
│   │   ├── evaluate.py           SP-type mapping, scoring, truth feature cache, report writer (CLI)
│   │   └── incidents.py          reported-incident checker (CLI + used by API)
│   ├── storage/gpkg.py           GeoPackage writers/readers (per-dataset path)
│   └── api/app.py                FastAPI app, dataset registry, endpoints, scheduler, static files
│
├── scripts/
│   ├── run_pipeline.py           end-to-end pipeline CLI (live or named historical dataset)
│   └── build_truth.py            pull SP ground-truth windows into data/truth/
│
├── web/
│   ├── index.html, style.css     layout (panel, legend, playback, source card, incident + model sections)
│   ├── app.js                    map logic, data loading, rendering, offline fallback
│   └── vendor/                   Leaflet 1.9.4 + MarkerCluster 1.5.3 (offline)
│
├── tests/
│   ├── test_rules.py             every rule in priority order, generic-OSM behaviour, site-level persistence
│   ├── test_features.py          grid/persistence, site join, VIIRS/MODIS normalisation
│   ├── test_sources.py           clustering, anomaly flag, rolling warm-up semantics
│   └── test_evaluate.py          truth mapping and scoring
│
├── data/                         (committed: raw FIRMS windows, flare + GEM downloads, caches; ignored: gpkg, truth, PBF)
│   ├── seed_sites.csv            curated sites incl. volcanoes and the ONGC Mori field
│   ├── incidents.csv             14 verified incidents with sources, radii, expected visibility
│   ├── raw/firms/<SOURCE>/       cached FIRMS windows (parquet)         raw/osm/india-latest.osm.pbf
│   ├── cache/                    landcover cells, WRI, OSM sites gpkg, NE countries, India boundary, logs
│   ├── truth/                    SP truth parquet + features_truth.parquet
│   └── frims_live.gpkg, frims_sp2025_26.gpkg
└── models/
    ├── gbm.joblib                truth-trained model (git-ignored)
    ├── eval_report.json          rules (tuning + hold-out) and ML metrics, confusion matrices, importances
    └── incident_report.json      incident check output
```

## 6. API surface

`GET /api/health` · `/api/datasets` · `/api/detections?dataset&days&end&classes&sources&bbox&label&limit` ·
`/api/sites` · `/api/sources` · `/api/sources/{id}/timeseries` · `/api/stats` · `/api/alerts` · `/api/eval` ·
`/api/incidents[?live=true]` · `/api/basemap` · `POST /api/refresh`.

## 7. Known limitations / next

- Alerts inside persistent complexes need a per-source adaptive threshold — evaluate on *new* incidents, not the 14 used here.
- Site database gaps (new plants, GIDC estates without OSM tags); a periodic OSM refresh and more curated entries.
- No geostationary source for India via FIRMS API.
- Not yet done: deployable demo (Docker / static export), judge-facing artefacts, repo hygiene (the initial commit
  tracked generated files under `data/` and `models/`; run `git rm -r --cached data models && git add data/*.csv models/*.json`
  once, then commit — the new `.gitignore` handles the rest).
