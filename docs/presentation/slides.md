---
marp: true
theme: default
paginate: true
---

# 🔥 FRIMS
## AI-based detection and classification of industrial fires and persistent thermal sources
NASA FIRMS · OpenStreetMap · ESA WorldCover

Smart India Hackathon · PS 26162 · NTRO · Disaster Management

---

## The problem

> "NASA FIRMS provides thermal anomaly detections but does not distinguish between industrial fires, gas flares, agricultural burning, mining activity and wildfires."

- **Deliverable i** — segregate industrial fires from forest and other natural fires
- **Deliverable ii** — GIS storage + visualisation as a map overlay

---

## Why it is hard

- FIRMS gives a point, a time, brightness, fire power, day/night — four passes a day at 375 m
- It cannot tell a stubble fire from the Jamnagar flare from Jharia's century-old coal fire
- **Nature does not burn in the same spot night after night for weeks. Industry does.**

---

## Three questions about every detection

1. **What is under it?** — ESA WorldCover 10 m land cover
2. **What is next to it?** — 44,381 industrial footprints (seed list, WRI power plants, GEM coal mines, NASA/EOG Nightfire flares, OSM)
3. **Does it keep happening?** — active days in the last 30/90 days per 1 km cell and per site, night fraction, usual fire power

Rule checklist → 7 classes · independent ML model trained on NASA labels → second opinion

---

## Architecture

FIRMS API (VIIRS ×3, MODIS; NRT + SP) · sites (seed + WRI + OSM PBF) · WorldCover · Natural Earth
→ **Ingest** (rate-limit guard, window cache, clip)
→ **Features** (grid, rolling persistence, proximity, land cover)
→ **Classify** (rules, source registry + anomalies, ML)
→ **GeoPackage** (QGIS) · **FastAPI** · **Leaflet UI** (offline-capable)

Python 3.12 · geopandas · rasterio · scikit-learn · FastAPI · Leaflet — no database server, no build step

---

## The rule checklist (first match wins)

| # | Evidence | Class |
|---|---|---|
| 1 | inside a known volcano | volcano |
| 2 | refinery/oil-gas/LNG site · mostly night · ≥ 10 of 90 days | gas flare |
| 3 | coalfield/mine/quarry · persistent | mining thermal |
| 4 | any named site · persistent | industrial – persistent source |
| 5 | inside a named site, not persistent — or ≥ 4σ hotter than usual | **industrial fire — ALERT** |
| 6 | persistent, no site on record | unmapped persistent source |
| 7 | cropland · day · ≤ 3 days | agricultural burning |
| 8 | forest/shrub/grass | wildfire |
| 9 | water / other | offshore / other |

---

## Validated against NASA's own answer key

- Truth: NASA standard-processing `type` (static source vs vegetation fire) — 2.8 M detections, 2 sensors, Aug 2025 → May 2026
- Rules tuned + ML trained on Nov–Dec 2025; **tested on Jan–May 2026 (1.49 M detections never seen)**

| Jan–May 2026 hold-out | Recall | Precision | F1 | Accuracy |
|---|---|---|---|---|
| Rules — industrial source | **98.6 %** | 79.8 % | **88.2 %** | 97.3 % |
| ML — industrial source | **97.7 %** | 78.7 % | **87.2 %** | 98.2 % |
| **Combined (rules + ML veto)** | **97.9 %** | **81.2 %** | **88.7 %** | 97.3 % |
| Rules — natural fire | 97.2 % | 99.9 % | 98.5 % | |

---

## It works on the places that matter

- Jamnagar refinery: **102 / 102** → gas flare
- Jharia coalfield: **2500 / 2513** → mining thermal
- Hazira LNG: **785 / 792** → gas flare
- Punjab/Haryana, 9–15 Nov 2025: **11,536** → agricultural burning
- **409** persistent thermal sources registered (Oct 2025 – May 2026)
- Barren Island: **639** → volcano

---

## Tested on real incidents — including the misses

14 news-reported incidents, Nov 2025 – Aug 2026, each with a source link

- **8 / 14 seen from space · 1 alert raised · 1 watch-tier hit**
- ✅ ONGC Mori-5 well blowout (Jan 2026, five days): 14 detections, all flagged industrial fire
- ⚠️ Seen but not separable: Vizag Steel ladle explosion, Mumbai High platform fire — inside always-hot complexes
- ⬜ Invisible: six short daytime / indoor events — 375 m pixels, four passes a day

Lesson: the site database, not the detector, limits alert recall.

---

## Live demo

1. Live map, last 7 days, data age in header
2. Dataset → Nov 2025, window end 15 Nov: Punjab yellow, Jharia/Jamnagar red
3. Jharia ring → persistent-source card + FRP sparkline
4. Incident table → ONGC Mori-5 → popup with evidence + ML probability
5. Model tab → confusion matrices
6. QGIS → `data/frims_live.gpkg` (deliverable ii)

---

## Efficient by design

- **14 %** peak use of the FIRMS 5000-tx budget; 100 % cache hits afterwards; ~40 tx per 3-hourly refresh
- **35 k detections / s** classified (1.18 M in 34 s)
- **82 s** to extract 43,369 industrial sites from the OSM India file
- **0 servers** — GeoPackage + FastAPI + Leaflet, offline-capable
- 17 automated tests; fully reproducible from a MAP_KEY

---

## From India to the world — and the honest limits

**Scale**: 100–500 k detections/day globally → one day in ~15 s, a year in ~1 h; 40–60 GB/year
**Production**: PostGIS + incremental rollups · Global Energy Monitor + Nightfire flare catalogue · vector tiles + MapLibre · on-prem / air-gapped

**Limits**: FIRMS latency ~3 h (geostationary needs a direct feed) · 375 m pixels miss short/indoor fires · alerts inside always-hot plants need adaptive thresholds, validated on *new* incidents · site database completeness is the lever

---

## Summary

- **(i)** 98 % recall, 89 % F1 against NASA's independent labels on 1.5 M unseen detections; explainable classes + independent ML opinion
- **(ii)** GeoPackage (QGIS) + interactive overlay with source registry, alerts, playback, offline mode
- Validated on real incidents, misses reported
- Laptop today, global on-prem tomorrow

**Thank you — questions?**
