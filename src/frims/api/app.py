"""FastAPI service: GeoJSON endpoints over one or more dataset GeoPackages + static Leaflet UI."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from frims import config
from frims.classify.evaluate import REPORT_PATH
from frims.classify.model import model_info
from frims.features.sources import source_timeseries

log = logging.getLogger(__name__)
app = FastAPI(title="FRIMS — Industrial Fire & Persistent Thermal Source Monitor", version="0.2.0")

_datasets: dict[str, dict] = {}
_state: dict = {"last_refresh": 0.0, "refreshing": False}
_lock = threading.Lock()
INDUSTRIAL = ["industrial_persistent", "gas_flare", "mining_thermal", "industrial_fire"]


def _load_one(path: Path) -> dict:
    det = gpd.read_file(path, layer="detections")
    det["acq_date"] = pd.to_datetime(det["acq_date"])
    det["acq_datetime"] = pd.to_datetime(det["acq_datetime"])
    if "is_night" not in det:
        det["is_night"] = det.daynight.eq("N")
    sites = gpd.read_file(path, layer="sites")
    try:
        sources = gpd.read_file(path, layer="persistent_sources")
    except Exception:  # noqa: BLE001
        sources = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return {"detections": det, "sites": sites, "sources": sources, "path": str(path),
            "loaded_at": datetime.now(timezone.utc).isoformat(), "min_date": det.acq_date.min().strftime("%Y-%m-%d"),
            "max_date": det.acq_date.max().strftime("%Y-%m-%d"), "n": int(len(det))}


def _load_all() -> None:
    found = {}
    for p in sorted(config.DATA_DIR.glob("frims_*.gpkg")):
        name = p.stem.replace("frims_", "")
        try:
            found[name] = _load_one(p)
            log.info("dataset %s: %d detections, %d sources", name, found[name]["n"], len(found[name]["sources"]))
        except Exception as e:  # noqa: BLE001
            log.warning("could not load %s: %s", p, e)
    legacy = config.DATA_DIR / "frims.gpkg"
    if "live" not in found and legacy.exists():
        found["live"] = _load_one(legacy)
    with _lock:
        _datasets.clear(); _datasets.update(found)
    if not found:
        log.warning("no datasets — run scripts/run_pipeline.py first")


def _scheduler() -> None:
    """Refresh the live dataset every REFRESH_EVERY_HOURS (cached windows make this ~40 FIRMS transactions)."""
    interval = config.REFRESH_EVERY_HOURS * 3600
    while True:
        time.sleep(interval)
        if not _state["refreshing"]:
            _state["refreshing"] = True; _state["last_refresh"] = time.time()
            log.info("scheduled refresh starting")
            _refresh_job()


@app.on_event("startup")
def _startup() -> None:
    _load_all()
    if config.REFRESH_EVERY_HOURS > 0:
        threading.Thread(target=_scheduler, daemon=True, name="frims-refresh").start()


def _ds(name: str) -> dict:
    if name not in _datasets:
        if not _datasets:
            raise HTTPException(503, "no data loaded — run scripts/run_pipeline.py")
        raise HTTPException(404, f"unknown dataset {name}; available: {list(_datasets)}")
    return _datasets[name]


def _window(det: pd.DataFrame, days: int, end: str | None) -> pd.DataFrame:
    ref = pd.Timestamp(end) if end else det.acq_date.max()
    return det[(det.acq_date > ref - pd.Timedelta(days=days)) & (det.acq_date <= ref)]


def _geojson(gdf: gpd.GeoDataFrame, limit: int | None = None) -> JSONResponse:
    if limit and len(gdf) > limit:
        gdf = gdf.sample(limit, random_state=0)
    return JSONResponse(content=json.loads(gdf.to_json(na="null", drop_id=True)))


@app.get("/api/health")
def health():
    now = datetime.now(timezone.utc)
    out = {}
    for k, v in _datasets.items():
        latest = v["detections"]["acq_datetime"].max()
        age_h = round((now - latest.tz_localize("UTC")).total_seconds() / 3600, 1) if pd.notna(latest) else None
        out[k] = {"n": v["n"], "min_date": v["min_date"], "max_date": v["max_date"],
                  "latest_detection_utc": latest.strftime("%Y-%m-%dT%H:%M") if pd.notna(latest) else None,
                  "data_age_hours": age_h, "loaded_at": v["loaded_at"]}
    return {"ok": bool(_datasets), "datasets": out, "bbox": config.BBOX,
            "refresh": {"every_hours": config.REFRESH_EVERY_HOURS, "running": _state["refreshing"],
                        "last_started": datetime.fromtimestamp(_state["last_refresh"], tz=timezone.utc).isoformat() if _state["last_refresh"] else None}}


@app.get("/api/datasets")
def datasets():
    return [{"name": k, "n": v["n"], "min_date": v["min_date"], "max_date": v["max_date"],
             "sources": int(len(v["sources"])), "path": v["path"]} for k, v in _datasets.items()]


@app.get("/api/detections")
def detections(dataset: str = "live", days: int = Query(7, ge=1, le=400), end: str | None = None,
               classes: str | None = None, sources: str | None = None, bbox: str | None = None,
               label: str = Query("label_final", pattern="^(label_rule|label_ml|label_final)$"), min_conf: float = 0.0,
               limit: int = Query(20000, le=150000)):
    det = _ds(dataset)["detections"]
    out = _window(det, days, end)
    if classes:
        out = out[out[label].isin(classes.split(","))]
    if sources:
        out = out[out.source.isin(sources.split(","))]
    if min_conf:
        out = out[out.conf_norm >= min_conf]
    if bbox:
        w, s, e, n = map(float, bbox.split(","))
        out = out.cx[w:e, s:n]
    cols = ["source", "satellite", "acq_datetime", "bt", "frp", "daynight", "confidence", "conf_norm", "sp_type",
            "n_days_90", "site_days_90", "night_frac_90", "site_type", "site_name", "dist_site_m", "landcover",
            "label_rule", "label_ml", "label_final", "ml_veto", "proba", "proba_industrial", "source_id", "anomaly_score", "explanation", "geometry"]
    out = out[[c for c in cols if c in out.columns]].copy()
    out["acq_datetime"] = out["acq_datetime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return _geojson(out, limit)


@app.get("/api/sites")
def sites(dataset: str = "live", types: str | None = None):
    s = _ds(dataset)["sites"]
    if types:
        s = s[s.site_type.isin(types.split(","))]
    return _geojson(s[["name", "site_type", "provider", "radius_m", "notes", "geometry"]])


@app.get("/api/sources")
def sources(dataset: str = "live", label: str | None = None):
    s = _ds(dataset)["sources"]
    if label and len(s):
        s = s[s.label.isin(label.split(","))]
    return _geojson(s)


@app.get("/api/sources/{sid}/timeseries")
def source_ts(sid: int, dataset: str = "live", days: int = 90):
    det = _ds(dataset)["detections"]
    ts = source_timeseries(det, sid, days)
    if ts.empty:
        raise HTTPException(404, "unknown source id")
    src = _ds(dataset)["sources"]
    meta = src[src.source_id == sid].drop(columns="geometry").iloc[0].to_dict() if len(src) else {}
    return {"source_id": sid, "meta": {k: (None if pd.isna(v) else v) for k, v in meta.items()},
            "daily": ts.to_dict(orient="records")}


@app.get("/api/persistent")
def persistent(dataset: str = "live"):
    """Backward-compatible alias for /api/sources."""
    return sources(dataset)


@app.get("/api/stats")
def stats(dataset: str = "live", days: int = Query(7, ge=1, le=400), end: str | None = None, label: str = "label_final",
          sources: str | None = None, night: bool = False, day: str | None = None):
    det = _ds(dataset)["detections"]
    sub = _window(det, days, end)
    
    if sources:
        sub = sub[sub.source.isin(sources.split(","))]
    if night:
        sub = sub[sub.is_night]
    if day:
        sub = sub[sub.acq_date.dt.strftime("%Y-%m-%d") == day]
        
    daily = sub.groupby([sub.acq_date.dt.strftime("%Y-%m-%d"), label]).size().unstack(fill_value=0)
    ind_mask = sub[label].isin(INDUSTRIAL) if label in ("label_rule", "label_final") else sub[label].eq("industrial")
    return {"dataset": dataset, "days": days, "total": int(len(sub)), "by_class": sub[label].value_counts().to_dict(),
            "by_source": sub.source.value_counts().to_dict(),
            "latest": (pd.Timestamp(end) if end else det.acq_date.max()).strftime("%Y-%m-%d"),
            "earliest": det.acq_date.min().strftime("%Y-%m-%d"), "daily": daily.to_dict(orient="index"),
            "industrial_share": round(float(ind_mask.mean()), 3) if len(sub) else 0,
            "anomalies": int((sub.get("anomaly_score", pd.Series(0, index=sub.index)) >= 4).sum()),
            "watch": int((sub.get("alert_tier", pd.Series("none", index=sub.index)) == "watch").sum())}


@app.get("/api/alerts")
def alerts(dataset: str = "live", days: int = Query(7, ge=1, le=400), end: str | None = None, tier: str = "alert"):
    """tier=alert: industrial_fire events (new event at a site, or FRP >= 4σ inside a known source).
    tier=watch: unusual-but-below-alert detections inside known sources (z >= 2, FRP >= 1.5x usual). tier=all: both."""
    det = _ds(dataset)["detections"]
    sub = _window(det, days, end)
    has_tier = "alert_tier" in sub.columns
    if tier == "watch" and has_tier:
        sub = sub[sub.alert_tier == "watch"]
    elif tier == "all" and has_tier:
        sub = sub[(sub.label_rule == "industrial_fire") | (sub.alert_tier == "watch")]
    else:
        sub = sub[sub.label_rule == "industrial_fire"]
    sub = sub.sort_values(["anomaly_score", "frp"], ascending=False)
    cols = ["acq_datetime", "site_name", "site_type", "frp", "bt", "source", "dist_site_m", "n_days_90", "site_days_90",
            "source_id", "anomaly_score", "alert_tier", "label_rule", "landcover", "geometry"]
    sub = sub[[c for c in cols if c in sub.columns]].copy()
    sub["acq_datetime"] = sub["acq_datetime"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return _geojson(sub, 500)


@app.get("/api/incidents")
def incidents(live: bool = False):
    """Reported real-world incidents vs what FRIMS saw. `live=true` recomputes against loaded datasets."""
    from frims.classify.incidents import REPORT_PATH as INC_PATH, check_incidents
    if live or not INC_PATH.exists():
        return check_incidents({k: v["detections"] for k, v in _datasets.items()})
    return json.loads(INC_PATH.read_text())


@app.get("/api/basemap")
def basemap():
    """Offline vector basemap: Natural Earth country outlines within the bbox (served from the local cache)."""
    from frims.ingest.boundary import NE_CACHE, _load_ne
    if not NE_CACHE.exists():
        raise HTTPException(404, "Natural Earth cache missing")
    ne = _load_ne()
    w, s_, e, n = config.BBOX
    sub = ne.cx[w - 5:e + 5, s_ - 5:n + 5]
    name_col = "ADMIN" if "ADMIN" in sub.columns else "NAME"
    return _geojson(sub[[name_col, "geometry"]].rename(columns={name_col: "name"}))


@app.get("/api/eval")
def evaluation():
    rep = json.loads(REPORT_PATH.read_text()) if REPORT_PATH.exists() else None
    return {"model": model_info(), "report": rep}


def _refresh_job() -> None:
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        from run_pipeline import run  # type: ignore
        run(days=90, train=False)
        _load_all()
    except Exception as e:  # noqa: BLE001
        log.exception("refresh failed: %s", e)
    finally:
        _state["refreshing"] = False


@app.post("/api/refresh")
def refresh(background: BackgroundTasks):
    now = time.time()
    if _state["refreshing"]:
        return {"status": "already running"}
    if now - _state["last_refresh"] < config.REFRESH_DEBOUNCE_MINUTES * 60:
        return {"status": "debounced", "retry_in_s": int(config.REFRESH_DEBOUNCE_MINUTES * 60 - (now - _state["last_refresh"]))}
    _state["last_refresh"] = now; _state["refreshing"] = True
    background.add_task(_refresh_job)
    return {"status": "started"}


app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
