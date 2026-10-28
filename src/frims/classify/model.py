"""ML classifier.

Preferred: trained on FIRMS standard-processing `type` ground truth (industrial vs natural) with a time-based
hold-out, see `train_on_truth`. Fallback (no truth available): mimic the rules (`train_on_rules`), which only
smooths edge cases and must not be reported as validation.
"""
from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from frims import config
from frims.classify.evaluate import INDUSTRIAL, coarse_from_label, coarse_from_sp, score
from frims.classify.rules import fallback_by_landcover
from frims.features.build import NUMERIC_FEATURES

log = logging.getLogger(__name__)
MODEL_PATH = config.MODELS_DIR / "gbm.joblib"
# features that leak the label through the site tables are fine (they are inputs at inference too);
# but sp_type itself must never be a feature.
FEATURES = [f for f in NUMERIC_FEATURES if f != "sp_type"]


def _X(df: pd.DataFrame) -> np.ndarray:
    return df.reindex(columns=FEATURES).fillna(0).astype(float).values


def _clf() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
                                          class_weight="balanced", random_state=0)


def train_on_truth(feats: pd.DataFrame, start: str, train_end: str, test_end: str, path: Path = MODEL_PATH) -> dict:
    df = feats[feats.sp_type.notna()].copy()
    df["y"] = coarse_from_sp(df.sp_type)
    df = df[df.y.isin(["industrial", "natural"])]
    tr = df[(df.acq_date >= pd.Timestamp(start)) & (df.acq_date <= pd.Timestamp(train_end))]
    te = df[(df.acq_date > pd.Timestamp(train_end)) & (df.acq_date <= pd.Timestamp(test_end))]
    if len(tr) < 100 or te.empty:
        raise SystemExit(f"not enough truth rows (train {len(tr)}, test {len(te)})")
    clf = _clf().fit(_X(tr), tr.y.astype(str).to_numpy(dtype=object))
    pred_te = pd.Series(clf.predict(_X(te)), index=te.index)
    res_test = score(te.y, pred_te)
    res_test["period"] = [str((pd.Timestamp(train_end) + pd.Timedelta(days=1)).date()), test_end]
    rules_te = score(te.y, coarse_from_label(te.label_rule))
    imp = dict(zip(FEATURES, np.round(_permutation_importance(clf, te), 4)))
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": clf, "features": FEATURES, "classes": list(clf.classes_), "trained_on": "sp_type",
                 "train_period": [start, train_end], "test_period": [train_end, test_end], "test": res_test}, path)
    log.info("ML trained on %d rows, tested on %d: industrial F1 %.3f (rules on same rows %.3f)", len(tr), len(te),
             res_test["per_class"]["industrial"]["f1"], rules_te["per_class"]["industrial"]["f1"])
    return {"train_rows": int(len(tr)), "test_rows": int(len(te)), "test": res_test,
            "rules_on_test_rows": rules_te, "feature_importance": imp}


def _permutation_importance(clf, te: pd.DataFrame, n: int = 3) -> np.ndarray:
    X = _X(te); y = te.y.astype(str).to_numpy(dtype=object)
    base = (clf.predict(X) == y).mean()
    rng = np.random.default_rng(0)
    out = np.zeros(X.shape[1])
    for j in range(X.shape[1]):
        drops = []
        for _ in range(n):
            Xp = X.copy(); rng.shuffle(Xp[:, j])
            drops.append(base - (clf.predict(Xp) == y).mean())
        out[j] = np.mean(drops)
    return out


def train_on_rules(df: pd.DataFrame, path: Path = MODEL_PATH) -> dict:
    y = coarse_from_label(df["label_rule"]).astype(str).to_numpy(dtype=object)
    if len(set(y)) < 2:
        return {}
    clf = _clf().fit(_X(df), y)
    joblib.dump({"model": clf, "features": FEATURES, "classes": list(clf.classes_), "trained_on": "rules"}, path)
    log.warning("model trained on rule labels only (no ground truth) — not a validation")
    return {"trained_on": "rules"}


def _combine(out: pd.DataFrame, veto_p: float = config.ML_VETO_P) -> pd.DataFrame:
    """label_final = label_rule, except a persistent-industrial rule label is demoted to the land-cover class when
    the truth-trained model is confident it is natural (p_industrial < veto_p). Alerts (industrial_fire) untouched."""
    veto = out["label_rule"].isin(INDUSTRIAL) & (out["proba_industrial"] < veto_p)
    if "site_type" in out:  # a detection at a catalogued flare is flaring; the model must not overrule the catalogue
        veto &= ~out["site_type"].isin(config.RULES.catalogued_flare_types)
    out["ml_veto"] = veto
    out["label_final"] = out["label_rule"].where(~veto, fallback_by_landcover(out.get("landcover", pd.Series("unknown", index=out.index))))
    return out


def predict(df: pd.DataFrame, path: Path = MODEL_PATH) -> pd.DataFrame:
    out = df.copy()
    if not path.exists():
        out["label_ml"] = coarse_from_label(out["label_rule"])
        out["proba"] = 1.0
        out["proba_industrial"] = (out["label_ml"] == "industrial").astype(float)
        return _combine(out, veto_p=-1.0)  # no independent model: never veto
    bundle = joblib.load(path)
    clf = bundle["model"]
    proba = clf.predict_proba(out.reindex(columns=bundle["features"]).fillna(0).astype(float).values)
    classes = list(clf.classes_)
    out["label_ml"] = np.array(classes)[proba.argmax(axis=1)]
    out["proba"] = proba.max(axis=1).round(3)
    out["proba_industrial"] = proba[:, classes.index("industrial")].round(3) if "industrial" in classes else 0.0
    return _combine(out) if bundle.get("trained_on") == "sp_type" else _combine(out, veto_p=-1.0)


def model_info(path: Path = MODEL_PATH) -> dict:
    if not path.exists():
        return {"trained_on": None}
    b = joblib.load(path)
    return {k: b.get(k) for k in ("trained_on", "train_period", "test_period", "test", "classes")}
