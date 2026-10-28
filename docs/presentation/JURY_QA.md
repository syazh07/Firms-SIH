# Jury preparation — our position, and the questions we will face

Read this the night before. Numbers are from `../../README.md`, `../TECHNICAL.md`, `../DATA_COLLECTION.md`
(2026-08-29). Say the uncomfortable numbers *before* the jury finds them.

---

## 1. Where we stand

### Strengths (lead with these)
- We don't just plot FIRMS dots — we explain each one, and we **prove it against NASA's own labels**:
  1.49 M unseen detections, 98 % recall / 89 % F1 for industrial sources (combined rules + ML-veto label).
- We **tested on 14 real reported incidents** and show the misses. Almost no hackathon team does this.
- Deliverable ii is solid and unglamorous: GeoPackage (QGIS), API, offline mode — nothing to break on stage.
- The core idea fits in one sentence: *nature doesn't burn in the same spot night after night; industry does.*
- Two independent opinions per detection (explainable rules + ML trained only on NASA labels) that agree 96–98 %.

### Exposures (own them first)
| Exposure | The number | The sentence to say before they ask |
|---|---|---|
| Precision | 81.2 % for "industrial source" (combined label; rules alone 79.8 %) | "About one in five of our industrial calls is a transient fire near industrial land — and a quarter of those sit in cells NASA itself labels inconsistently. We chose recall — a missed refinery fire costs more than a false look — and every call carries its evidence so an analyst clears it in seconds." |
| Alerts on incidents | 1 of 14 raised an alert (+1 on the watch tier); 8 seen | "Satellites saw 8 of 14. Alerts need a site on file — the ONGC well was our clean win; the site database, not the detector, is the lever, and we show which incidents fell through and why." |
| Latency | FIRMS NRT ≈ 3 h | "Three hours is the FIRMS ceiling. Ten-minute cadence exists on INSAT-3D / MSG-IODC but not through the FIRMS API — that is a data agreement, and our pipeline is source-agnostic." |
| ML vs rules | F1 87.2 % vs 88.2 % | "The model is a check, not the product. Its value is independence: it never saw our rules, so its agreement is corroboration." |
| Post-hoc site | Mori field added after the event | "We added the ONGC Mori field after the blowout exposed the gap. It is a real, public field, the file says when and why we added it — that is the process working." |

### What moves the odds
1. Rehearse the 3-minute demo until it is boring (timed, twice).
2. Put the two uncomfortable numbers on the slides with the explanation (done — slides 7 and 9).
3. A deployed URL so judges/mentors can poke at it after the session.
4. 10–20 more curated sites (recent large plants, GIDC estates) — cheap, raises the incident hit rate.

---






## 2. Question bank

### A. Problem understanding
**Q: Doesn't FIRMS already have a "type" field for static sources?**
Yes — in the *standard-processing* product, which arrives weeks to months later and only says "static land
source", not flare vs coal fire vs plant. We produce the class in near-real-time and we use NASA's late field as
our ground truth, which is the strongest validation available.

**Q: What exactly is a "persistent thermal source"?**
A 1 km cell (or an industrial site) that is hot on ≥ 10 different days in the last 90. We cluster such cells
(DBSCAN, 2 km) into a registry entry with footprint, first/last seen, night fraction and fire-power statistics.
409 of them over India for Oct 2025 – May 2026.

**Q: Why India and not global?**
The PS organisation is NTRO; India has the richest test cases (Jharia, Jamnagar, Punjab stubble season). The code
is bbox-driven; `docs/PRODUCTION.md` sizes the global version (100–500 k detections/day, ~15 s of compute per day).

### B. Data
**Q: Which sensors?**
VIIRS on S-NPP, NOAA-20, NOAA-21 (375 m, ~4 passes/day combined) and MODIS Aqua/Terra (1 km) in near-real-time;
VIIRS NOAA-20 and S-NPP standard-processing for truth and history. Landsat is US/Canada-only in FIRMS; GOES is
Americas-only; MSG-IODC is not exposed by the API.

**Q: How do you handle the FIRMS rate limit?**
5000 transactions / 10 min, ~1 per 700 rows. We check the budget before batches, learn the real cost per sensor,
sleep when near the limit, cache every completed 5-day window forever and compose any range from cache. Peak use
so far: 14 % of one window; every re-run since is 0 network calls.

**Q: Where do the industrial sites come from?**
Five layers: a curated list (53 refineries, coalfields, steel, LNG, offshore fields, volcanoes), WRI Global Power
Plant Database (391 thermal plants), Global Energy Monitor's coal-mine tracker (394 mines), NASA/EOG's VIIRS
Nightfire global flare survey (355 flare sites in India), and 43,188 OSM features extracted from the Geofabrik India file in 82 s with pyosmium. Curated layers take priority in
the join.

**Q: Land cover source and resolution?**
ESA WorldCover 2021, 10 m, read at a 640 m overview per 1 km cell — one read per 3° tile instead of 736 k point
requests.

**Q: How current is the data on screen?**
The header shows data age; the service refreshes every 3 hours automatically (~40 transactions). FIRMS itself is
~3 h behind the satellite.

### C. Method
**Q: Why rules at all in an "AI" problem statement?**
Because an alert must be explainable to an operator: "coalfield site Jharia at 0 m; 54 active days / 90; night
fraction 0.77". Rules give that; the ML model gives an independent probability. Both are on every detection.

**Q: What are the ML model's inputs and what did it learn?**
21 features: brightness, FRP, confidence, day/night, rolling 30/90-day activity (days, detections, night fraction,
FRP mean/max, recurrence), site distance / inside flag / site-level persistence, land-cover class. Permutation
importance: active days (0.038), night fraction (0.016), site persistence (0.011), mean FRP (0.007) — persistence
carries the signal; instantaneous brightness alone carries none.

**Q: Which algorithm and why?**
`HistGradientBoostingClassifier` (scikit-learn): strong on tabular data, handles mixed scales, trains on 268 k rows
in seconds, no GPU. Deep learning on imagery was out of scope because the input is point detections, not images.

**Q: How do you avoid the model just copying your rules?**
It never sees rule labels. It is trained on NASA's `type` only. The 96–98 % agreement is therefore evidence, not
circularity.

**Q: How do you detect a fire inside a plant that is always hot?**
Each registered source has a normal FRP (mean, σ). A detection ≥ 4σ above it, ≥ 2× the mean and ≥ 10 MW — or a
daytime detection at a night-only flare — becomes an alert. Vizag (z = 1.3) and Mumbai High (z = 2.1) show the
threshold is conservative, so a **watch tier** (z ≥ 2, ≥ 1.5× usual) surfaces such cases to an analyst without
diluting alert precision — Mumbai High lands there. An adaptive per-source threshold is the next step, validated
on new incidents.

**Q: Why 10 days out of 90? Why 1.5 km?**
Tuned on Nov–Dec 2025 against NASA truth and then frozen before the Jan–May hold-out. Every threshold is a named
constant in one config file; we can show the sensitivity if asked.

### D. Validation
**Q: How do you know 89 % is real and not overfit?**
Time split: tuned/trained on Nov–Dec 2025, tested on Jan–May 2026 (1.49 M detections, including the spring fire
season, which is the hardest period). On the tuning months alone the rules scored 88 % — so no inflation. The ML
veto threshold gives the same F1 (0.887–0.889) anywhere between 0.2 and 0.5, so it is not a tuned coincidence.

**Q: Confusion matrix?**
Hold-out, rules: of 92,308 true industrial sources we label 90,999 industrial (98.6 %); of 1.40 M natural fires we
label 20,914 as industrial — those are the precision cost; a share of them are intermittent flares NASA calls "vegetation".

**Q: What about one-off industrial fires — does the 89 % cover them?**
No, and we say so. NASA's field cannot distinguish a transient industrial fire from any other transient fire, so
those are validated separately on the 14 real incidents.

**Q: Only 14 incidents?**
Every one is dated, geocoded and linked to a news/agency source; we refused to include anything unverifiable.
The framework is a CSV — adding incidents is a one-line edit and the report regenerates.

**Q: Why did six incidents show nothing?**
Short daytime fires, indoor explosions, a cylinder blast: below the 375 m / four-passes-a-day detection floor.
That is physics, not our pipeline, and it is the right thing to tell an operator.

### E. Engineering
**Q: Stack?**
Python 3.12, geopandas/shapely, rasterio, scikit-learn, FastAPI, Leaflet (vendored). Storage is GeoPackage. No
database server, no Node build, no cloud dependency; runs on a laptop or air-gapped.

**Q: Throughput?**
35 k detections/s through the full pipeline (1.18 M in 34 s); API 300 req/s for dashboard calls, ~26 req/s for
780 KB GeoJSON payloads on one worker.

**Q: Does it work without internet?**
Yes — once data is pulled. Leaflet is local, datasets and model are on disk, and the map switches to an offline
vector basemap automatically when tile servers fail.

**Q: Tests? Reproducibility?**
17 unit tests (rules incl. flare, mining and veto cases, features and join priority, clustering, evaluation). From a fresh clone and a MAP_KEY, `SETUP.md` reproduces
everything; all pulls are cached and budgeted.

**Q: Security / data sensitivity for NTRO?**
All inputs are public datasets; nothing leaves the machine except FIRMS/OSM/WorldCover downloads. Keys live in
`.env`. Production design keeps everything on-prem.

### F. Scale and production
**Q: Will it scale to the world?**
Compute yes (a day of global data in ~15 s). Architecture needs four changes: PostGIS with incremental daily
rollups, a global site layer (Global Energy Monitor + VIIRS Nightfire flare catalogue), vector tiles + MapLibre,
bulk ingest from NASA LANCE. Detailed in `docs/PRODUCTION.md`; 2–3 weeks of work.

**Q: Real-time?**
Not with FIRMS. Geostationary INSAT-3D (ISRO) or MSG-IODC would give 10-minute cadence; the pipeline is
source-agnostic, the feed is a data agreement.

**Q: Cost to run?**
Free data, one VM. Even global runs on an 8-core machine; the FIRMS key is free.

### G. Comparison and impact
**Q: What can NTRO do with this that they cannot do with FIRMS today?**
Maintain a registry of every persistent thermal source in the country with history; get an alert when a named
facility deviates from its normal signature; separate industrial from agricultural/wildfire signal in bulk; export
to their GIS.

**Q: Existing tools — Global Forest Watch, SkyTruth flaring, VIIRS Nightfire?**
Nightfire catalogues flares globally (we plan to use it as a site source); GFW is vegetation-focused. None
classify Indian detections into flare / coal fire / plant / industrial fire with alerts and local site context.

**Q: Who is the user?**
A monitoring analyst: opens the map, sees classes and alerts, drills into a source's history, exports to QGIS.
The rules' evidence string is written for that person.

**Q: Why did adding the flare catalogue *lower* your score?**
Because NASA's truth calls intermittent flaring "vegetation fire" — its static-source flag needs persistence. Typing a
detection at a catalogued flare as `gas_flare` is correct for an operator and cut spurious alerts at flares from
261 to 19 per 90 days; it costs ≈ 0.4 pt of measured F1. We chose the right class over the higher number and say so.

### H. Demo and process
**Q: Can we see it on a place we choose?**
Yes — type coordinates into the map or pick any incident/source. (Have Jharia, Jamnagar, Bombay High, Punjab ready.)

**Q: What went wrong during development?**
The FIRMS 5-day cap (tutorial said 10); Overpass unusable so we moved to a PBF extract; OSM polygons initially
*lowered* precision until we stopped generic areas from conferring persistence; rolling windows needed a warm-up
fix; a village geocode 25 km off nearly hid the Mori-5 blowout. All in `PROJECT_NOTES.md`.

**Q: What would you do with two more weeks?**
Deploy it, add the Nightfire flare catalogue and GEM sites, adaptive anomaly thresholds validated on fresh
incidents, and INSAT-3D if ISRO grants access.

---

## 3. Three sentences to remember

1. *"Nature doesn't burn in the same spot night after night; industry does — we measure that."*
2. *"Validated against NASA's own labels on 1.5 million detections we never trained on, and against 14 real
   incidents, misses included."*
3. *"Runs on this laptop, opens in QGIS, works without internet, and scales to the world with a database swap."*
