"""Feature engineering: grid cells, *rolling* persistence statistics, site proximity and land cover.

Persistence is computed per detection over the 30/90 days that precede that detection (inclusive), so the same
code serves the live pipeline (latest state) and historical evaluation (each day sees only its own past).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from frims import config
from frims.ingest.sites import site_footprints

log = logging.getLogger(__name__)

NUMERIC_FEATURES = [
    "bt", "bt2", "frp", "conf_norm", "is_night", "scan", "track",
    "n_days_30", "n_days_90", "n_det_90", "night_frac_90", "frp_mean_90", "frp_max_90", "bt_mean_90",
    "recurrence_90", "frp_ratio", "in_site", "dist_site_m", "landcover_code", "site_days_90", "days_90_eff",
]
_DAY_STRIDE = 1_000_000  # encodes (group, day) into one sortable int64 key


def add_grid(df: pd.DataFrame, grid_deg: float = config.GRID_DEG) -> pd.DataFrame:
    out = df.copy()
    gi = np.floor(out.latitude / grid_deg).astype(int)
    gj = np.floor(out.longitude / grid_deg).astype(int)
    out["grid_id"] = gi.astype(str) + "_" + gj.astype(str)
    out["cell_lat"] = (gi + 0.5) * grid_deg
    out["cell_lon"] = (gj + 0.5) * grid_deg
    return out


def _rolling_window_stats(df: pd.DataFrame, group_col: str, window: int, prefix: str,
                          extra: bool = False) -> pd.DataFrame:
    """For every row: stats over the rows of the same group whose acq_date lies in (date - window, date].

    Warm-up: while the dataset holds fewer than `window` days of history before a row, the window is extended
    forward so that it always spans `window` days. Persistence describes the *location*, so using later
    detections for the first days of a dataset is legitimate (evaluation only scores after a full warm-up).
    """
    day = (df.acq_date - pd.Timestamp("2000-01-01")).dt.days.to_numpy()
    dmin = int(day.min())
    grp = pd.factorize(df[group_col])[0]
    daily = pd.DataFrame({"g": grp, "d": day, "n": 1, "night": df.is_night.astype(int).to_numpy(),
                          "frp": df.frp.fillna(0).to_numpy(), "bt": df.bt.fillna(0).to_numpy()})
    agg = daily.groupby(["g", "d"]).agg(n=("n", "sum"), night=("night", "sum"), frp=("frp", "sum"),
                                        frp_max=("frp", "max"), bt=("bt", "sum")).reset_index()
    keys = agg.g.to_numpy() * _DAY_STRIDE + agg.d.to_numpy()          # sorted because groupby sorts
    cum = {c: np.concatenate([[0], np.cumsum(agg[c].to_numpy())]) for c in ("n", "night", "frp", "bt")}
    lo_day = np.maximum(day - window + 1, dmin)
    hi_day = np.maximum(day, lo_day + window - 1)
    hi = np.searchsorted(keys, grp * _DAY_STRIDE + hi_day, side="right")
    lo = np.searchsorted(keys, grp * _DAY_STRIDE + lo_day, side="left")
    first_day = agg.groupby("g").d.min().to_numpy()[grp]
    out = pd.DataFrame(index=df.index)
    out[f"n_days_{prefix}"] = hi - lo
    if extra:
        n = cum["n"][hi] - cum["n"][lo]
        out[f"n_det_{prefix}"] = n
        out[f"night_frac_{prefix}"] = (cum["night"][hi] - cum["night"][lo]) / np.maximum(n, 1)
        out[f"frp_mean_{prefix}"] = (cum["frp"][hi] - cum["frp"][lo]) / np.maximum(n, 1)
        out[f"bt_mean_{prefix}"] = (cum["bt"][hi] - cum["bt"][lo]) / np.maximum(n, 1)
        # window max via per-group cumulative maximum is not decomposable; use a rolling max over daily maxima
        fm = agg.frp_max.to_numpy()
        out[f"frp_max_{prefix}"] = [fm[a:b].max() if b > a else 0.0 for a, b in zip(lo, hi)]
        span = np.clip(hi_day - np.maximum(first_day, lo_day) + 1, 1, window)
        out[f"recurrence_{prefix}"] = out[f"n_days_{prefix}"] / span
    return out


def persistence_stats(df: pd.DataFrame, ref_date: pd.Timestamp | None = None) -> pd.DataFrame:
    """Static per-cell stats relative to one reference date (kept for tests / ad-hoc analysis)."""
    ref = ref_date or df.acq_date.max()
    out = []
    for w in config.PERSISTENCE_WINDOWS_DAYS:
        sub = df[df.acq_date > ref - pd.Timedelta(days=w)]
        g = sub.groupby("grid_id")
        stats = pd.DataFrame({f"n_days_{w}": g.acq_date.nunique(), f"n_det_{w}": g.size()})
        if w == 90:
            stats["night_frac_90"] = g.is_night.mean()
            stats["frp_mean_90"] = g.frp.mean()
            stats["frp_max_90"] = g.frp.max()
            stats["bt_mean_90"] = g.bt.mean()
            span_days = ((ref - g.acq_date.min()).dt.days + 1).clip(lower=1)
            stats["recurrence_90"] = stats["n_days_90"] / span_days.clip(upper=w)
        out.append(stats)
    return pd.concat(out, axis=1).reset_index()


def add_rolling_persistence(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.join(_rolling_window_stats(out, "grid_id", 30, "30"))
    out = out.join(_rolling_window_stats(out, "grid_id", 90, "90", extra=True))
    return out


def join_sites(df: pd.DataFrame, sites: gpd.GeoDataFrame) -> pd.DataFrame:
    """Nearest industrial footprint for each detection: in_site flag, distance (m) and site_type.

    Curated sites (seed list, WRI) are matched first; the ~40k OSM polygons only serve detections that no curated
    footprint claims. Otherwise a small OSM polygon inside a refinery complex would win the tie and lose the
    complex-wide persistence.
    """
    pts = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    metric_pts = pts.to_crs("EPSG:7755")
    foot = site_footprints(sites)
    if "provider" not in foot.columns:
        foot = foot.assign(provider="seed")
    foot = foot[["site_id", "name", "site_type", "provider", "geometry"]].rename(columns={"name": "site_name"}).to_crs("EPSG:7755")
    # provider priority: hand-verified complexes first, then WRI plants, then catalogued flares, then OSM.
    # A refinery flare point (1 km) must not steal a detection from the refinery complex it sits in.
    order = ("seed", "wri", "gem", "nightfire")
    layers = [foot[foot.provider == prov] for prov in order] + [foot[~foot.provider.isin(order)]]
    cols = ["site_id", "site_name", "site_type", "dist_site_m"]
    result = pd.DataFrame(index=df.index, columns=cols)
    remaining = metric_pts
    for layer in layers:
        if layer.empty or remaining.empty:
            continue
        j = gpd.sjoin_nearest(remaining, layer.drop(columns="provider"), how="left", distance_col="dist_site_m",
                              max_distance=config.SITE_MATCH_DISTANCE_M)
        j = j[~j.index.duplicated(keep="first")]
        hit = j[j.site_id.notna()]
        result.loc[hit.index, cols] = hit[cols].values
        remaining = remaining.loc[~remaining.index.isin(hit.index)]
    out = pd.DataFrame(df).copy()
    out["site_id"] = pd.to_numeric(result["site_id"], errors="coerce")
    out["site_name"] = result["site_name"].where(result["site_id"].notna(), None)
    out["site_type"] = result["site_type"].where(result["site_id"].notna(), "none").fillna("none")
    out["dist_site_m"] = pd.to_numeric(result["dist_site_m"], errors="coerce").fillna(50_000.0)
    out["near_site"] = out["site_id"].notna()
    out["in_site"] = out["near_site"] & (out["dist_site_m"] <= 0.0)
    return out


def add_site_persistence(df: pd.DataFrame) -> pd.DataFrame:
    """A refinery spans many grid cells; count active days over the whole site footprint too (rolling 90 d)."""
    out = df.copy()
    out["site_days_90"] = 0
    mask = out.near_site & out.site_id.notna()
    if mask.any():
        sub = out.loc[mask, ["site_id", "acq_date", "is_night", "frp", "bt"]]
        stats = _rolling_window_stats(sub, "site_id", 90, "site")
        out.loc[mask, "site_days_90"] = stats["n_days_site"].to_numpy()
    generic = out["site_type"].isin(config.RULES.generic_site_types)
    out.loc[generic, "site_days_90"] = 0  # tuned on SP truth: generic OSM areas must not confer persistence
    out["days_90_eff"] = out[["n_days_90", "site_days_90"]].max(axis=1)
    return out


def add_landcover(df: pd.DataFrame, online: bool = True) -> pd.DataFrame:
    from frims.ingest.landcover import sample_landcover
    lc = sample_landcover(df[["grid_id", "cell_lat", "cell_lon"]], online=online)
    return df.merge(lc, on="grid_id", how="left")


def build_features(detections: pd.DataFrame, sites: gpd.GeoDataFrame, landcover_online: bool = True) -> pd.DataFrame:
    df = add_grid(detections).sort_values("acq_datetime").reset_index(drop=True)
    df = add_rolling_persistence(df)
    df["frp_ratio"] = (df["frp"] / df["frp_mean_90"].replace(0, np.nan)).fillna(1.0)
    df = join_sites(df, sites)
    df = add_site_persistence(df)
    df = add_landcover(df, online=landcover_online)
    for c in NUMERIC_FEATURES:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    log.info("features built for %d detections, %d cells", len(df), df.grid_id.nunique())
    return df
