# FRIMS in plain language — how a hot dot becomes "flare", "coal fire" or "crop burning"

A non-technical explanation of the pipeline, the detection rules and the machine-learning model. For the
engineering detail see `PROJECT_NOTES.md`; for the numbers see `../README.md`.

## What NASA gives us

A satellite passes over India about four times a day and reports every spot on the ground that is unusually hot —
a **dot with a location, a time, how hot, how bright, and day/night**. That's it. It does not say *what* is
burning. A crop-stubble fire in Punjab, a wildfire in Uttarakhand, the flare on top of the Jamnagar refinery, and
the coal seams that have been smouldering under Jharia for a century all look the same: a hot dot.

## What we add to each dot — three questions

1. **What's underneath it?** We look up the land cover at that spot (a satellite land map at 10 m resolution):
   cropland, forest, bare ground, built-up, or water?
2. **What's nearby?** We keep a list of industrial places — refineries, steel plants, power stations, coalfields,
   LNG terminals, offshore oil fields, 394 coal mines from Global Energy Monitor, NASA's catalogue of 355 gas flares
   seen from space, plus ~43,000 industrial areas and quarries traced from OpenStreetMap. Is
   this dot inside, or within 1.5 km of, one of them?
3. **Does this keep happening here?** We split India into ~1 km squares and, for each dot, count how many
   *different days* in the past 90 days that same square (or the whole industrial site it belongs to) was hot, and
   how often that was at night.

The third question is the heart of it. **Nature doesn't burn in the same spot night after night for weeks;
industry does.** A field burns once and it's done. A wildfire moves. A gas flare, a steel plant, a burning coal
mine glows in the same place 30, 60, 80 nights out of 90.

## The rules — a checklist, top to bottom, first match wins

Think of a fire-safety officer looking at each dot with the three answers in hand:

1. Inside a known volcano's footprint? → **volcano** (Barren Island).
2. Near a refinery / oil-gas / LNG site, mostly at night, and hot on ≥ 10 of the last 90 days? → **gas flare**.
3. Near a coalfield / mine / quarry, persistent — and that exact 1 km square keeps recurring? → **mining thermal**
   (coal-seam fires). A farm fire that merely lies inside a big coalfield's circle does not qualify.
4. Near any named industrial site and persistent? → **industrial – persistent source** (a plant that is simply
   always hot).
5. Inside a named industrial site but *not* persistent — something that isn't normally hot is hot today? →
   **industrial fire — ALERT**.
6. Persistent but no site on our list? → **persistent source, unmapped** (we've found something industry-like
   that nobody told us about — brick kilns, an unlisted plant).
7. Over water → offshore/other. Cropland, daytime, only seen a day or two → **agricultural burning**.
   Forest / shrub / grass → **wildfire**.

Every threshold ("10 days", "1.5 km", "mostly at night") is a number in one config file, so it can be tuned.

On top of that, for every persistent source we learn its *normal* heat level. If a dot inside it is far hotter than
that source ever is (statistically 4 standard deviations, at least twice its average and over 10 MW), that is also
an **alert** — "the refinery is hotter than a refinery should be". That is how a fire *inside* an always-hot plant
can be caught.

## The ML model — a second opinion that learned from NASA

The rules are written by us. The ML model is **not**: it is a *gradient-boosted decision tree* model
(scikit-learn's `HistGradientBoostingClassifier`) — in plain terms, a few hundred small decision trees that vote,
each asking questions like "is the night-fraction above 0.6? is the fire power above 4 MW? is it within 800 m of a
site?" and learning the best questions from data.

Where did it learn the right answers? NASA's slower, higher-quality "standard processing" version of the same data
(available months later) tags each dot with a **type**: *presumed vegetation fire* or *other static land source*
(= a persistent industrial thing). We fed the model 268,000 dots from Nov–Dec 2025 with our 21 features and NASA's
type as the answer key — **never showing it our rules' labels**. Then we tested it on Jan–May 2026, which it had
never seen (1.5 million dots).

So on the map every dot carries two labels: **Rules** (explainable, seven classes, the one we alert on) and
**ML** (industrial / natural with a probability). When both agree you can trust it; when they disagree it is worth
a human look.

## How we know it works

- Against NASA's own labels on five unseen months: the rules score **87%** and the model **87%** (F1) at separating
  persistent industrial sources from natural fires; letting the model veto a rule label when it is confident the
  fire is natural lifts the combined result to **89%**, still catching ~98% of real industrial sources.
- Against 14 real, news-reported incidents: 8 were visible from space, and the 5-day ONGC well blowout in
  Konaseema (Jan 2026) was correctly flagged as an industrial-fire alert. The ones we missed were short or indoor —
  a 375 m-pixel satellite passing four times a day simply cannot see those. We say that openly rather than hide it.

## One-sentence version

*For every hot spot NASA reports, we check what's under it, what industry is next to it, and whether that exact
spot keeps being hot night after night; a plain checklist turns that into "flare / mine / plant / industrial fire /
crop burn / wildfire", and a separate machine-learned model that studied NASA's own answer key gives an independent
second opinion.*
