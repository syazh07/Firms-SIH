"""India boundary for clipping: land polygon + 100 km offshore allowance, minus neighbouring countries' land."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

from frims import config

log = logging.getLogger(__name__)

NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
NE_CACHE = config.CACHE_DIR / "ne_50m_admin_0_countries.geojson"
BOUNDARY_CACHE = config.CACHE_DIR / "india_boundary.gpkg"
OFFSHORE_BUFFER_M = 200_000  # Bombay High is ~150 km off Mumbai


def _load_ne() -> gpd.GeoDataFrame:
    if not NE_CACHE.exists():
        r = requests.get(NE_URL, timeout=120)
        r.raise_for_status()
        NE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NE_CACHE.write_bytes(r.content)
    return gpd.read_file(NE_CACHE)


def india_boundary(refresh: bool = False) -> gpd.GeoDataFrame:
    """Single-polygon GeoDataFrame (EPSG:4326): India land ∪ 100 km sea buffer, excluding other countries' land."""
    if BOUNDARY_CACHE.exists() and not refresh:
        return gpd.read_file(BOUNDARY_CACHE)
    ne = _load_ne()
    name_col = "ADMIN" if "ADMIN" in ne.columns else "NAME"
    from shapely import make_valid
    india_ll = ne[ne[name_col] == "India"]
    rough = india_ll.geometry.union_all().buffer(2.0)  # ~200 km in degrees, just to pre-select neighbours
    others_ll = ne[(ne[name_col] != "India") & ne.intersects(rough)]
    india = india_ll.to_crs("EPSG:7755").geometry.apply(make_valid).union_all()
    neighbours = others_ll.to_crs("EPSG:7755").geometry.apply(make_valid).union_all()
    zone = india.buffer(OFFSHORE_BUFFER_M).difference(neighbours).union(india)
    gdf = gpd.GeoDataFrame({"name": ["India + 200 km offshore"]}, geometry=[zone], crs="EPSG:7755").to_crs("EPSG:4326")
    gdf.to_file(BOUNDARY_CACHE, driver="GPKG")
    return gdf


def clip_to_india(df: pd.DataFrame, lat_col: str = "latitude", lon_col: str = "longitude") -> pd.DataFrame:
    """Keep rows whose point falls inside the India zone. Works for detections and for site tables alike."""
    if df.empty:
        return df
    zone = india_boundary()
    pts = gpd.GeoDataFrame(df[[lat_col, lon_col]], geometry=[Point(xy) for xy in zip(df[lon_col], df[lat_col])],
                           crs="EPSG:4326")
    inside = gpd.sjoin(pts, zone[["geometry"]], how="inner", predicate="within").index
    kept = df.loc[df.index.isin(inside)]
    log.info("clip to India: kept %d / %d rows", len(kept), len(df))
    return kept
