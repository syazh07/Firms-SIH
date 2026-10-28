# FRIMS — Industrial Fire & Persistent Thermal Source Monitor

Smart India Hackathon problem **26162 (NTRO)**: NASA FIRMS reports thermal anomalies but cannot tell a refinery flare
from a wildfire or crop-stubble burning. FRIMS ingests FIRMS detections over India, enriches them with industrial
infrastructure (OSM, WRI power plants, a curated site list) and ESA WorldCover land cover, classifies every detection
into **industrial persistent / gas flare / mining thermal / industrial fire (alert) / agricultural burn / wildfire /
volcano / offshore-other**, stores the result in a GeoPackage and serves it as a map overlay.

```
FIRMS API ─┐
OSM / WRI ─┼─► ingest ─► features (grid persistence, site proximity, land cover) ─► rules ─► ML ─► data/frims.gpkg
WorldCover ┘                                                                                      │
                                                                      FastAPI /api/* (GeoJSON) ◄──┘ ─► web/ Leaflet UI
```

## Documentation

- [`docs/SETUP.md`](docs/SETUP.md) — install, run, flags, offline mode, troubleshooting
- [`docs/PROJECT_NOTES.md`](docs/PROJECT_NOTES.md) — what we built, how it works, decisions, file-by-file map
- [`docs/PRODUCTION.md`](docs/PRODUCTION.md) — measured throughput and the production / global-scale stack
- [`docs/HOW_IT_WORKS_PLAIN.md`](docs/HOW_IT_WORKS_PLAIN.md) — the pipeline, rules and ML model in plain language
- [`docs/TECHNICAL.md`](docs/TECHNICAL.md) — exact algorithms, formulas, schemas, constants, API contract
- [`docs/DATA_COLLECTION.md`](docs/DATA_COLLECTION.md) — data sources, collection mechanics, volumes and efficiency figures
- [`docs/presentation/`](docs/presentation/) — jury deck (self-contained HTML + Marp Markdown) and the live-demo script

## Setup

```bash
uv sync --group dev              # Python 3.12 env (uv installs the interpreter)
echo "MAP_KEY=<your FIRMS key>" > .env   # https://firms.modaps.eosdis.nasa.gov/api/map_key
```

## Run

```bash
uv run python scripts/run_pipeline.py --backfill-days 90 --dry-run   # planned FIRMS calls + transaction estimate
uv run python scripts/run_pipeline.py --backfill-days 90             # live: NRT last 90 d → data/frims_live.gpkg
uv run python scripts/run_pipeline.py --dataset sp2025_26 --source-set sp --start 2025-10-15 --end 2026-05-31
                                                                      # historical SP (both VIIRS) → data/frims_sp2025_26.gpkg
uv run uvicorn frims.api.app:app --reload                             # http://127.0.0.1:8000 (dataset dropdown)
uv run pytest
```

Flags: `--offline` (cached FIRMS only), `--no-osm`, `--no-landcover`, `--no-clip` (keep neighbours), `--no-train`.

### Validation against ground truth

```bash
uv run python scripts/build_truth.py [--source VIIRS_SNPP_SP] [--start … --end …]   # SP data with the `type` attribute
uv run python -m frims.classify.evaluate --eval-end 2026-05-31   # rules + ML vs truth → models/eval_report.json
```

FIRMS standard-processing VIIRS labels every pixel with `type` (0 presumed vegetation fire, 1 volcano, **2 other
static land source**, 3 offshore). SP cannot see a one-off industrial fire (it is type 0 like any transient fire), so
the comparison is **static industrial source** (`industrial_persistent`, `gas_flare`, `mining_thermal`) vs
**transient fire** (`agricultural_burn`, `wildfire`, `industrial_fire`). Truth = VIIRS NOAA-20 **and** S-NPP SP,
Aug 2025 – May 2026 (Aug–Oct is persistence warm-up only). Rules were tuned and the ML model trained on Nov–Dec 2025;
**Jan–May 2026 is held out** (1,497,284 detections, including the spring fire season):

| | static P | static R | static F1 | transient P | transient R | transient F1 | accuracy |
|---|---|---|---|---|---|---|---|
| Rules, Nov–Dec 2025 (tuning, n=269,791) | 0.818 | 0.962 | 0.884 | 0.993 | 0.929 | 0.960 | 0.932 |
| Rules, Jan–May 2026 hold-out | 0.798 | 0.986 | 0.882 | 0.999 | 0.974 | 0.986 | 0.973 |
| ML, Jan–May 2026 hold-out | 0.787 | 0.977 | 0.872 | 0.999 | 0.983 | 0.990 | 0.982 |
| **Combined (rules + ML veto), Jan–May 2026 hold-out** | 0.812 | 0.979 | **0.887** | 0.999 | 0.974 | 0.986 | 0.973 |

**Combined** is what the map shows by default (`label_final`): the rule label, except that a persistent-industrial
rule label is demoted to its land-cover class when the independent model gives p(industrial) < 0.3 (vetoes
0.2 % of hold-out detections). Alerts (`industrial_fire`) are never vetoed. The gain is flat for veto
thresholds 0.2–0.5, so it is not a tuned coincidence.

The ML model (`HistGradientBoosting`) is trained on SP `type` and never sees `type` as a feature; `label_ml` and
`proba_industrial` are therefore independent of the rules. Full report: `models/eval_report.json`, `/api/eval`.

### Gas-flare catalogue

The EOG / ORNL DAAC **Global Gas Flare Survey (VIIRS Nightfire, 2012–2019; DOI 10.3334/ORNLDAAC/1874)** adds 355
named flare sites inside India (offshore Mumbai High, Gujarat, Assam, KG basin). Download the `flare_list.csv`
files (free Earthdata sign-in) into `data/raw/flares/`; `ingest/flares.py` picks them up. Catalogued flares get their own
site type (`flare_site`): any detection there is `gas_flare` even when intermittent, and the ML veto never overrules the
catalogue. **Trade-off, stated plainly:** NASA's truth labels intermittent flaring as "vegetation fire", so this costs
≈ 0.4 pt of measured hold-out F1 at the time (combined 0.888 → 0.884; later recovered to 0.887 by the cell-level mining rule) while: static detections typed `gas_flare` rise 1,593 →
4,159, spurious `industrial_fire` alerts at flares fall 261 → 19 per 90 days, and incidents attribute to named flares
(Mumbai High). We chose the correct class over the higher score.

### Coal-mine positions (optional)

`ingest/gem.py` loads the **Global Energy Monitor Global Coal Mine Tracker** (CC BY 4.0; spreadsheet via their
download form) from `data/raw/gem/` as `coal_mine` sites whose footprint is the reported mine area (1–8 km), ranked above the Nightfire and OSM layers.
Loaded (Aug 2026 release, 394 mines in India): no change to the measured industrial-vs-natural score — the seed
coalfields and OSM polygons already covered those areas — but 3,551 live detections (90 named mines) now carry a
mine name, and mining labels rose by ~450 in basins the seed list did not cover (Singareni/Godavari).

### OSM industrial polygons

Download `https://download.geofabrik.de/asia/india-latest.osm.pbf` (~1.7 GB) to `data/raw/osm/`; the pipeline
extracts `landuse=industrial|quarry`, `man_made=works|flare|petroleum_well`, `power=plant`, `industrial=*`,
`resource=coal` with pyosmium (43 369 features for India, ~80 s) and caches `data/cache/osm_sites.gpkg`. Overpass
remains a fallback. Generic `landuse=industrial` / `works` / `quarry` polygons never confer site-level persistence and
do not raise `industrial_fire` alerts on their own (tuned on the SP truth set).

## FIRMS rate limit

A MAP_KEY allows **5000 transactions per 10 minutes** and each call costs roughly 1 transaction per ~700 rows.
`FirmsClient` checks `mapkey_status`, estimates each call from observed cost, sleeps when the budget is nearly spent,
caches every completed window to `data/raw/firms/` (never re-fetched) and fails fast on 4xx. The NRT area endpoint
accepts at most **5 days per call**, so a 90-day, 4-sensor backfill is 72 calls.

## Classification rules (first match wins)

| # | Condition | Label |
|---|---|---|
| 1 | land cover = water and not an offshore platform | `offshore_other` |
| 2 | refinery / oil-gas / LNG site, night fraction ≥ 0.5, ≥ 10 active days in 90 d | `gas_flare` |
| 3 | coalfield / mine / quarry site, persistent **and the detection's own 1 km cell recurring (≥ 10 d)** | `mining_thermal` |
| 4 | industrial site, ≥ 10 active days in 90 d (outside the polygon the cell itself must recur ≥ 5 d) | `industrial_persistent` |
| 5 | industrial site, not persistent | `industrial_fire` (alert) |
| 6 | cropland, daytime, ≤ 3 active days | `agricultural_burn` |
| 7 | tree cover / shrub / grass / wetland | `wildfire` |
| 8 | within a known volcano (Barren Island, Narcondam) — checked first | `volcano` |
| 9 | else | `offshore_other` |

A `HistGradientBoosting` model is trained on the rule labels (weak supervision) to provide probabilities and smooth
edge cases; both `label_rule` and `label_ml` are stored.

Persistence features are rolling per detection (the 90 days before it); during the first 90 days of a dataset the
window is extended forward so early detections at a flare are not mistaken for new events.

### Event-level check against reported incidents

`data/incidents.csv` holds dated, source-linked industrial incidents (refinery/petrochemical fires, a 5-day ONGC well
blowout, steel-plant and boiler explosions, …). `python -m frims.classify.incidents` reports, per incident, what
FRIMS saw within its radius in [−1 d, +N d]: detections, max FRP, labels, whether an `industrial_fire` / anomaly
alert fired, and the 30-day baseline. Results: `models/incident_report.json`, `/api/incidents`, UI "Incident check".
Satellites pass ~4×/day at 375 m, so short or indoor events can legitimately be missed — `expected_visible` records
the prior expectation.

Result on 14 incidents (Nov 2025 – Aug 2026): **8 seen by satellite, 1 raised an alert, 1 more on the watch tier**
(of the 3 expected to be visible: 2 seen, 1 alerted).
Honest reading:

- The 5-day **ONGC Mori-5 blowout** (Jan 2026) is the clean success: 14 night detections, all `industrial_fire`,
  alert fired. It was first labelled `wildfire` because no site covered the Mori field; the ONGC Mori gas field was
  added to `seed_sites.csv` afterwards (noted in the file) — site-database completeness is the limiting factor.
- Events inside already-hot complexes (Vizag Steel ladle explosion 19.8 MW, z = 1.3; Bhilai; Mumbai High platform
  fire z = 2.1; Haldia 1.4 MW) are *seen* but not distinguishable from routine operation at the 4σ alert threshold.
  The **watch tier** (z ≥ 2) surfaces Mumbai High to an analyst without lowering alert precision.
- Two hits with a wide search radius (Vedanta Sakti, Real Ispat) are inconclusive: the detections are mostly farm
  fires in the 25 km circle.
- Six events (short daytime fires, indoor blasts, a cylinder explosion) left no detection at all.

### Refresh schedule and freshness

The API refreshes the `live` dataset in-process every `FRIMS_REFRESH_HOURS` (default 3; cached windows keep it to
~40 FIRMS transactions) and `/api/health` reports `data_age_hours` per dataset, shown in the UI header.

### How good can it get against this truth?

NASA's `type` is itself noisy: 24 % of the most persistent night-time cells (≥ 30 active days, night fraction ≥ 0.9)
are labelled "vegetation fire", and 27 % of our false positives sit in cells NASA *sometimes* calls static. Brick
kilns and small industry are the likely explanation. The realistic ceiling against this truth is therefore ≈ 0.92,
not 1.0.

## Persistent-source registry and alerts

Recurring cells (≥ 10 active days / 90 d) are clustered (DBSCAN, 2 km) into **sources** with footprint, dominant
class, linked site, first/last seen and FRP statistics. Two alert types feed `/api/alerts`:
non-persistent detections at an industrial site (`industrial_fire`) and detections inside a known source whose FRP
exceeds its mean + 4σ (and ≥ 2× mean, ≥ 10 MW) or that appear by day at a night-only flare (`anomaly_score`).
A lower **watch** tier (z ≥ 2, FRP ≥ 1.5× usual, ≥ 5 MW; `alert_tier = watch`, `/api/alerts?tier=watch`) surfaces
unusual-but-not-alert-grade activity to analysts — it is where the Vizag and Mumbai High incidents sit.

## Outputs

`data/frims_<dataset>.gpkg` layers: `detections`, `sites`, `persistent_sources` — open directly in QGIS.
Detections are clipped to India + 200 km offshore (Natural Earth boundary, neighbours' land excluded).
API (all take `?dataset=`): `/api/datasets`, `/api/detections?days=7&classes=gas_flare,industrial_fire`,
`/api/sources`, `/api/sources/{id}/timeseries`, `/api/sites`, `/api/stats`, `/api/alerts`, `/api/eval`,
`POST /api/refresh` (debounced 15 min).
