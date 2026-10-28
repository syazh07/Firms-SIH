# FRIMS — jury demo script (target 3 minutes, max 5)

## Before you walk in

```bash
cd frims
uv run uvicorn frims.api.app:app --host 127.0.0.1 --port 8000     # leave running
open http://127.0.0.1:8000                                          # Chrome, full screen (⌘⇧F)
open docs/presentation/FRIMS_jury_deck.html                         # second tab; → / ← to navigate
```

- Confirm `/api/health` shows both datasets (`live`, `sp2025_26`).
- If the venue has **no internet**: choose **Basemap → Offline vector**; everything else works unchanged.
  Do NOT press "Refresh from FIRMS".
- Have QGIS open with `data/frims_live.gpkg` loaded (layers: detections, sites, persistent_sources) as a backup
  visual and for deliverable ii.
- Zoom levels that look good: India 5, Gujarat coast 8, Jharia 11, Konaseema 11.

## Talk track (each step ≈ 30 s)

| # | Do | Say |
|---|---|---|
| 1 | Deck slides 2–4 (problem, three questions) | "FIRMS gives hot dots, not causes. Nature doesn't burn in the same spot night after night; industry does. We ask three questions of every dot: what's under it, what's next to it, does it keep happening." |
| 2 | Switch to the map. Live dataset, **7 DAYS**. Point at header (data age) and the legend counts. | "Last seven days over India — clipped to the border, every dot classified, and you can see the data is N hours old and refreshes itself every three hours." |
| 3 | **Dataset → sp2025_26**, **Window end → 2025-11-15**, **7 DAYS**, untick *Cluster*. | "Peak stubble season. Punjab and Haryana go yellow — agricultural burning — while Jharia, Jamnagar and Hazira stay red and orange. Same satellite, different answer." |
| 4 | Zoom to Jharia (23.75, 86.42). Click the dashed ring. | "This is a persistent-source record: 84 active days out of 90, night fraction 0.77, a coalfield polygon from our site database, and its 90-day fire-power history. This is what 'monitoring a persistent thermal source' means." |
| 5 | Scroll the panel to **Incident check**, click **ONGC Mori-5**. Click a magenta dot. | "We tested against 14 real reported incidents. This is the ONGC well blowout in January that burned for five days — every detection flagged industrial fire. The popup shows the evidence and the ML probability." |
| 6 | Scroll to **Model**. | "The independent check: NASA's own slower product labels static sources. On 1.5 million detections we never trained on, we recover 98 % of industrial sources at 89 % F1 with the combined label. The misses are on the slide too." |
| 7 | Alt-tab to QGIS. | "And deliverable two: the same data as a GeoPackage in QGIS — no proprietary format, no server." |
| 8 | Deck slide 12–13. | "Runs on this laptop; PostGIS and a global site catalogue take it worldwide. Thank you." |

## Likely questions — short answers

- **How is this different from FIRMS?** FIRMS detects; we explain. Persistence + site proximity + land cover →
  class + alert; FIRMS has none of that, and its own "static source" flag arrives months later.
- **How accurate?** 97.9 % recall / 81.2 % precision (F1 88.7 %, combined label) for industrial sources against
  NASA's independent labels on a five-month hold-out; 8 of 14 real incidents seen, 1 alerted; misses documented.
- **Why rules and ML?** Rules are explainable and what we alert on; the ML model is trained only on NASA labels,
  so agreement between the two (96–98 %) is genuine corroboration, not an echo.
- **Real-time?** FIRMS NRT is ~3 h; ISRO INSAT-3D or EUMETSAT MSG-IODC feeds would bring 10-minute cadence but
  need a data agreement — not exposed by the FIRMS API.
- **Will it scale?** 35 k detections/s on a laptop; global volume is 100–500 k/day. Move storage to PostGIS with
  incremental rollups and the site layer to Global Energy Monitor + the Nightfire flare catalogue.
- **Air-gapped?** Yes — no SaaS; Leaflet vendored; offline basemap; all inputs are downloadable files.
- **What did you get wrong?** Alerts inside always-hot plants (Vizag, Mumbai High) sit below our 4σ threshold; the
  Mori field was missing from our site list until the incident showed it — we added it and say so.

## If something breaks

| Problem | Do |
|---|---|
| Map blank | Basemap → Offline vector; or reload — Leaflet is local. |
| Server not running | `uv run uvicorn frims.api.app:app --port 8000` (10 s to load). |
| No time | Steps 3 → 5 only (stubble season, Jharia ring, Mori-5). |
| Projector kills colours | Deck slide 6 has the legend; Basemap → Dark. |
