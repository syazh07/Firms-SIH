"""End-to-end: FIRMS ingest -> clip -> sites -> features -> rules -> sources/anomalies -> ML -> GeoPackage.

Datasets: `--dataset live` (default, NRT last N days) or a named historical dataset, e.g.
  --dataset nov2025 --source-set sp --start 2025-10-15 --end 2026-01-15
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from frims import config  # noqa: E402
from frims.classify import model as ml  # noqa: E402
from frims.classify.rules import classify_rules, explain  # noqa: E402
from frims.features.build import build_features  # noqa: E402
from frims.features.sources import build_sources, flag_anomalies  # noqa: E402
from frims.ingest.boundary import clip_to_india  # noqa: E402
from frims.ingest.firms import FirmsClient  # noqa: E402
from frims.ingest.sites import build_sites  # noqa: E402
from frims.storage import gpkg  # noqa: E402

log = logging.getLogger("pipeline")


def run(days: int = 90, dry_run: bool = False, offline: bool = False, no_osm: bool = False, train: bool = True,
        landcover: bool = True, dataset: str = "live", source_set: str = "nrt", start: str | None = None,
        end: str | None = None, clip: bool = True) -> None:
    config.ensure_dirs()
    sources_list = config.SOURCE_SETS[source_set]
    client = FirmsClient(dry_run=dry_run)
    if start and end:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        frames = [client.fetch_range(src, s, e) for src in sources_list]
        frames = [f for f in frames if not f.empty]
        det = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not det.empty:
            det = det.drop_duplicates(subset=["source", "latitude", "longitude", "acq_date", "acq_time"])
    else:
        det = client.load_cached(days, sources_list) if offline else client.fetch_all(days, sources_list)
    if dry_run:
        total = sum(p["est_tx"] for p in client.planned)
        for p in client.planned:
            print(f"  {p['source']:<18} {p['start']} +{p['days']}d  ~{p['est_tx']} tx")
        print(f"planned {len(client.planned)} calls, ~{total} transactions (budget {config.TX_LIMIT_DEFAULT}/10 min)")
        return
    if det.empty:
        log.error("no detections available"); return
    if clip:
        det = clip_to_india(det)
    log.info("[%s] %d detections from %s", dataset, len(det), det.source.value_counts().to_dict())

    sites = build_sites(include_osm=not no_osm)
    if clip:
        sites = sites[sites.index.isin(clip_to_india(sites).index)]
    log.info("%d sites (%s)", len(sites), sites.provider.value_counts().to_dict())

    feats = build_features(det, sites, landcover_online=landcover)
    feats["label_rule"] = classify_rules(feats)
    sources, source_id = build_sources(feats)
    feats = flag_anomalies(feats, sources, source_id)
    log.info("rule labels: %s", feats.label_rule.value_counts().to_dict())

    if train and not ml.MODEL_PATH.exists():
        ml.train_on_rules(feats)
    feats = ml.predict(feats)
    feats["explanation"] = feats.apply(explain, axis=1)

    path = config.gpkg_path(dataset)
    gpkg.write_detections(feats, path)
    gpkg.write_sites(sites, path)
    gpkg.write_sources(sources, path)
    log.info("dataset %s -> %s (%d sources)", dataset, path, len(sources))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=90)
    ap.add_argument("--dataset", default="live")
    ap.add_argument("--source-set", choices=list(config.SOURCE_SETS), default="nrt")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--dry-run", action="store_true", help="print planned FIRMS calls and estimated tx, no network")
    ap.add_argument("--offline", action="store_true", help="use cached FIRMS data only (no FIRMS network calls)")
    ap.add_argument("--no-osm", action="store_true")
    ap.add_argument("--no-train", action="store_true")
    ap.add_argument("--no-landcover", action="store_true", help="skip WorldCover sampling (cached cells still used)")
    ap.add_argument("--no-clip", action="store_true", help="keep detections outside India")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(a.backfill_days, a.dry_run, a.offline, a.no_osm, not a.no_train, not a.no_landcover, a.dataset,
        a.source_set, a.start, a.end, not a.no_clip)
