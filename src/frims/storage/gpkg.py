"""GeoPackage persistence — the GIS store that QGIS / ArcGIS can open directly. One file per dataset."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from frims import config

log = logging.getLogger(__name__)

DETECTION_COLUMNS = [
    "source", "satellite", "instrument", "latitude", "longitude", "bt", "bt2", "frp", "acq_date", "acq_datetime",
    "daynight", "confidence", "conf_norm", "sp_type", "grid_id", "n_days_30", "n_days_90", "n_det_90", "night_frac_90",
    "frp_mean_90", "frp_max_90", "recurrence_90", "frp_ratio", "site_type", "site_name", "dist_site_m", "in_site",
    "near_site", "site_days_90", "days_90_eff", "landcover", "landcover_code", "label_rule", "label_ml", "proba",
    "proba_industrial", "label_final", "ml_veto", "source_id", "anomaly_score", "alert_tier", "explanation",
]


def write_detections(df: pd.DataFrame, path: Path) -> None:
    cols = [c for c in DETECTION_COLUMNS if c in df.columns]
    gdf = gpd.GeoDataFrame(df[cols].copy(), geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    for c in ("acq_date", "acq_datetime"):
        if c in gdf:
            gdf[c] = pd.to_datetime(gdf[c]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    for c in ("latitude", "longitude", "bt", "bt2", "frp", "conf_norm", "proba", "proba_industrial", "anomaly_score"):
        if c in gdf:
            gdf[c] = pd.to_numeric(gdf[c], errors="coerce").astype(float)
    for c in ("in_site", "near_site", "ml_veto"):
        if c in gdf:
            gdf[c] = gdf[c].astype(int)
    for c in ("sp_type", "source_id"):
        if c in gdf:
            gdf[c] = gdf[c].astype("float").fillna(-1).astype(int)
    if path.exists():
        path.unlink()
    gdf.to_file(path, layer="detections", driver="GPKG")
    log.info("wrote %d detections -> %s", len(gdf), path)


def write_sites(sites: gpd.GeoDataFrame, path: Path) -> None:
    sites.to_file(path, layer="sites", driver="GPKG")


def write_sources(sources: gpd.GeoDataFrame, path: Path) -> None:
    if sources.empty:
        return
    sources.to_file(path, layer="persistent_sources", driver="GPKG")


def read_layer(layer: str, path: Path) -> gpd.GeoDataFrame:
    return gpd.read_file(path, layer=layer)
