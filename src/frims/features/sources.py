"""Persistent-source registry: cluster recurring grid cells into named thermal sources with history & anomaly flags."""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import MultiPoint, Point
from sklearn.cluster import DBSCAN

from frims import config

log = logging.getLogger(__name__)
CLUSTER_EPS_M = 2000.0
ANOMALY_SIGMA = 4.0
ANOMALY_MIN_FRP_MW = 10.0
ANOMALY_MIN_RATIO = 2.0
WATCH_SIGMA = 2.0          # 'watch' tier: unusual but below alert grade (surfaced to analysts, not alerted)
WATCH_MIN_RATIO = 1.5


def build_sources(feats: pd.DataFrame, min_days: int = config.RULES.persistent_min_days_90) -> tuple[gpd.GeoDataFrame, pd.Series]:
    """Returns (sources GeoDataFrame, per-detection source_id Series aligned to feats.index)."""
    source_id = pd.Series(pd.array([pd.NA] * len(feats), dtype="Int64"), index=feats.index, name="source_id")
    ref = feats.acq_date.max()
    latest = feats[feats.acq_date > ref - pd.Timedelta(days=90)]
    cells = latest.groupby("grid_id").agg(n_days_90=("n_days_90", "max"), cell_lat=("cell_lat", "first"),
                                          cell_lon=("cell_lon", "first"))
    cells = cells[cells.n_days_90 >= min_days]
    if cells.empty:
        return gpd.GeoDataFrame(columns=["source_id"], geometry=[], crs="EPSG:4326"), source_id
    pts = gpd.GeoDataFrame(cells, geometry=[Point(xy) for xy in zip(cells.cell_lon, cells.cell_lat)],
                           crs="EPSG:4326").to_crs("EPSG:7755")
    xy = np.c_[pts.geometry.x, pts.geometry.y]
    cells["cluster"] = DBSCAN(eps=CLUSTER_EPS_M, min_samples=1).fit_predict(xy)
    cell_to_cluster = cells["cluster"].to_dict()

    member = feats[feats.grid_id.isin(cells.index)]
    source_id.loc[member.index] = member.grid_id.map(cell_to_cluster).astype("Int64")
    g = member.assign(cluster=member.grid_id.map(cell_to_cluster)).groupby("cluster")
    rows = []
    for cid, grp in g:
        recent = grp[grp.acq_date > ref - pd.Timedelta(days=90)]
        label = recent.label_rule.mode().iat[0] if len(recent) else grp.label_rule.mode().iat[0]
        site = recent.loc[recent.near_site, "site_name"].dropna()
        cell_pts = cells[cells.cluster == cid]
        geom = MultiPoint([Point(xy) for xy in zip(cell_pts.cell_lon, cell_pts.cell_lat)]).convex_hull.buffer(0.006)
        rows.append({
            "source_id": int(cid), "label": label, "n_cells": len(cell_pts),
            "site_name": site.mode().iat[0] if len(site) else None,
            "site_type": recent.loc[recent.near_site, "site_type"].mode().iat[0] if recent.near_site.any() else "none",
            "landcover": recent.landcover.mode().iat[0] if len(recent) else "unknown",
            "active_days_90": int(recent.acq_date.nunique()), "n_det_90": int(len(recent)),
            "night_frac_90": float(recent.is_night.mean()) if len(recent) else 0.0,
            "frp_mean_90": float(recent.frp.mean()) if len(recent) else 0.0,
            "frp_std_90": float(recent.frp.std(ddof=0)) if len(recent) else 0.0,
            "frp_max_90": float(recent.frp.max()) if len(recent) else 0.0,
            "first_seen": grp.acq_date.min().strftime("%Y-%m-%d"), "last_seen": grp.acq_date.max().strftime("%Y-%m-%d"),
            "centroid_lat": float(cell_pts.cell_lat.mean()), "centroid_lon": float(cell_pts.cell_lon.mean()),
            "geometry": geom,
        })
    sources = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326").sort_values("active_days_90", ascending=False)
    log.info("persistent sources: %d (from %d cells)", len(sources), len(cells))
    return sources, source_id


def flag_anomalies(feats: pd.DataFrame, sources: gpd.GeoDataFrame, source_id: pd.Series) -> pd.DataFrame:
    """Within a known source, a detection far above its usual FRP (≥ mean + 4σ, ≥ 2× mean, ≥ 10 MW) is an abnormal event."""
    out = feats.copy()
    out["source_id"] = source_id
    out["anomaly_score"] = 0.0
    out["alert_tier"] = "none"
    if sources.empty:
        return out
    stats = sources.set_index("source_id")[["frp_mean_90", "frp_std_90", "night_frac_90"]]
    m = out.source_id.notna()
    s = stats.reindex(out.loc[m, "source_id"].astype(int))
    z = (out.loc[m, "frp"].to_numpy() - s.frp_mean_90.to_numpy()) / np.maximum(s.frp_std_90.to_numpy(), 1.0)
    out.loc[m, "anomaly_score"] = np.round(z, 2)
    daytime_at_night_source = m & (~out.is_night.astype(bool)) & (out.source_id.map(stats.night_frac_90) >= 0.9)
    frp_ok = (out.frp >= ANOMALY_MIN_FRP_MW) & (out.frp >= ANOMALY_MIN_RATIO * out.source_id.map(stats.frp_mean_90).fillna(0))
    anomalous = ((out.anomaly_score >= ANOMALY_SIGMA) & frp_ok) | daytime_at_night_source
    out.loc[anomalous & out.label_rule.isin(["industrial_persistent", "gas_flare", "mining_thermal"]), "label_rule"] = "industrial_fire"  # volcano excluded
    watch = m & ~anomalous & (out.anomaly_score >= WATCH_SIGMA) & (out.frp >= WATCH_MIN_RATIO * out.source_id.map(stats.frp_mean_90).fillna(0)) & (out.frp >= 5.0)
    out["alert_tier"] = np.select([anomalous, watch], ["alert", "watch"], "none")
    log.info("anomalous events flagged: %d; watch-tier: %d", int(anomalous.sum()), int(watch.sum()))
    return out


def source_timeseries(feats: pd.DataFrame, sid: int, days: int = 90) -> pd.DataFrame:
    sub = feats[feats.source_id == sid]
    ref = feats.acq_date.max()
    sub = sub[sub.acq_date > ref - pd.Timedelta(days=days)]
    daily = sub.groupby(sub.acq_date.dt.strftime("%Y-%m-%d")).agg(n=("frp", "size"), frp_max=("frp", "max"),
                                                                  frp_sum=("frp", "sum"), night=("is_night", "mean"))
    return daily.reset_index().rename(columns={"acq_date": "date"})
