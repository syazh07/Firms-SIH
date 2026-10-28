"""Evaluate rule and ML labels against FIRMS standard-processing `type` ground truth.

Coarse classes: "industrial" = static industrial source (SP type 2 = other static land source) vs "natural" =
transient fire (SP type 0 = presumed vegetation fire) vs other (type 1 volcano, 3 offshore).

SP has no notion of a one-off industrial fire — a transient event inside a refinery is still type 0 — so our
`industrial_fire` alerts are compared as transient events and cannot be verified by this truth set.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from frims import config

log = logging.getLogger(__name__)
INDUSTRIAL = {"industrial_persistent", "gas_flare", "mining_thermal"}   # static sources
NATURAL = {"agricultural_burn", "wildfire", "industrial_fire"}           # transient events
# "volcano" and "offshore_other" map to "other" (SP types 1 and 3)
COARSE = ["industrial", "natural", "other"]
REPORT_PATH = config.MODELS_DIR / "eval_report.json"


def coarse_from_label(labels: pd.Series) -> pd.Series:
    return pd.Series(np.select([labels.isin(INDUSTRIAL), labels.isin(NATURAL)], ["industrial", "natural"], "other"),
                     index=labels.index)


def coarse_from_sp(sp_type: pd.Series) -> pd.Series:
    t = pd.to_numeric(sp_type, errors="coerce")
    return pd.Series(np.select([t == 2, t == 0], ["industrial", "natural"], "other"), index=sp_type.index)


def _prf(y_true: pd.Series, y_pred: pd.Series, cls: str) -> dict:
    tp = int(((y_true == cls) & (y_pred == cls)).sum())
    fp = int(((y_true != cls) & (y_pred == cls)).sum())
    fn = int(((y_true == cls) & (y_pred != cls)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "support": tp + fn}


def score(y_true: pd.Series, y_pred: pd.Series) -> dict:
    cm = pd.crosstab(y_true, y_pred).reindex(index=COARSE, columns=COARSE, fill_value=0)
    return {
        "n": int(len(y_true)),
        "accuracy": round(float((y_true == y_pred).mean()), 4),
        "per_class": {c: _prf(y_true, y_pred, c) for c in COARSE},
        "confusion": {"rows_true": COARSE, "cols_pred": COARSE, "matrix": cm.values.tolist()},
    }


def evaluate(feats: pd.DataFrame, start: str, end: str, label_col: str = "label_rule") -> dict:
    sub = feats[(feats.acq_date >= pd.Timestamp(start)) & (feats.acq_date <= pd.Timestamp(end)) & feats.sp_type.notna()]
    y_true = coarse_from_sp(sub.sp_type)
    y_pred = coarse_from_label(sub[label_col]) if label_col in ("label_rule", "label_final") else sub[label_col].astype(str)
    res = score(y_true, y_pred)
    res["period"] = [str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date())]
    res["label_col"] = label_col
    if label_col == "label_rule":
        # how each fine rule class is composed by SP type (diagnostic for threshold tuning)
        res["fine_class_vs_sp"] = pd.crosstab(sub.label_rule, y_true).to_dict(orient="index")
    return res


def load_truth_features(truth_dir: Path = config.DATA_DIR / "truth", landcover_online: bool = True) -> pd.DataFrame:
    from frims.classify.rules import classify_rules
    from frims.features.build import build_features
    from frims.ingest.sites import build_sites
    files = sorted(truth_dir.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no truth files in {truth_dir}; run scripts/build_truth.py first")
    raw = pd.concat([pd.read_parquet(f) for f in files if not f.name.startswith("features_")], ignore_index=True)
    raw = raw.drop_duplicates(subset=["source", "latitude", "longitude", "acq_date", "acq_time"])
    cache = truth_dir / "features_truth.parquet"
    if cache.exists() and cache.stat().st_mtime > max(f.stat().st_mtime for f in files):
        return pd.read_parquet(cache)
    sites = build_sites(include_osm=(config.CACHE_DIR / "osm_sites.gpkg").exists())
    feats = build_features(raw, sites, landcover_online=landcover_online)
    feats["label_rule"] = classify_rules(feats)
    feats.to_parquet(cache, index=False)
    return feats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-start", default="2025-11-01")
    ap.add_argument("--eval-end", default="2026-01-31")
    ap.add_argument("--train-end", default="2025-12-31",
                    help="rules were tuned and ML trained on eval-start..train-end; everything after is held out")
    ap.add_argument("--no-train", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    feats = load_truth_features()
    report = {"rules": evaluate(feats, a.eval_start, a.train_end, "label_rule"),
              "rules_holdout": evaluate(feats, pd.Timestamp(a.train_end) + pd.Timedelta(days=1), a.eval_end, "label_rule")}
    for k in ("rules", "rules_holdout"):
        log.info("%s %s industrial P/R/F1 %s", k.upper(), report[k]["period"], report[k]["per_class"]["industrial"])
    if not a.no_train:
        from frims.classify import model as ml
        report["ml"] = ml.train_on_truth(feats, a.eval_start, a.train_end, a.eval_end)
        log.info("ML (test %s..%s) industrial %s", a.train_end, a.eval_end, report["ml"]["test"]["per_class"]["industrial"])
        # combined label (rules + ML veto) on the hold-out only — the model has seen the tuning months
        hold_start = str((pd.Timestamp(a.train_end) + pd.Timedelta(days=1)).date())
        combined = ml.predict(feats[(feats.acq_date >= hold_start) & (feats.acq_date <= a.eval_end)])
        report["combined_holdout"] = evaluate(combined, hold_start, a.eval_end, "label_final")
        report["combined_holdout"]["veto_rate"] = round(float(combined.ml_veto.mean()), 4)
        log.info("COMBINED (rules + ML veto p<%.2f) hold-out industrial %s", config.ML_VETO_P,
                 report["combined_holdout"]["per_class"]["industrial"])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str))
    log.info("wrote %s", REPORT_PATH)


if __name__ == "__main__":
    main()
