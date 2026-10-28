"""Industrial site layer: curated seed sites + WRI Global Power Plant DB + OSM (see osm.py)."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

from frims import config

log = logging.getLogger(__name__)

WRI_FUEL_TO_TYPE = {
    "Coal": "power_plant", "Gas": "power_plant", "Oil": "power_plant", "Biomass": "power_plant",
    "Waste": "power_plant", "Cogeneration": "power_plant", "Petcoke": "power_plant",
}


def load_seed_sites(path: Path = config.SEED_SITES_CSV) -> gpd.GeoDataFrame:
    df = pd.read_csv(path)
    df["provider"] = "seed"
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    return gdf


def load_wri_power_plants(bbox=config.BBOX, cache_dir: Path = config.CACHE_DIR) -> gpd.GeoDataFrame:
    cache = cache_dir / "wri_power_plants_IND.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        try:
            r = requests.get(config.WRI_POWER_PLANTS_URL, timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("WRI power plant DB unavailable: %s", e)
            return gpd.GeoDataFrame(columns=["name", "site_type", "latitude", "longitude", "radius_m", "provider"],
                                    geometry=[], crs="EPSG:4326")
        from io import StringIO
        full = pd.read_csv(StringIO(r.text), low_memory=False)
        w, s, e, n = bbox
        df = full[(full.longitude.between(w, e)) & (full.latitude.between(s, n))]
        df = df[df["primary_fuel"].isin(WRI_FUEL_TO_TYPE)]
        df = df[["name", "primary_fuel", "capacity_mw", "latitude", "longitude"]].copy()
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache, index=False)
    df = df.copy()
    df["site_type"] = df["primary_fuel"].map(WRI_FUEL_TO_TYPE)
    # bigger plants get a bigger footprint; thermal plants sit on 1-3 km sites
    df["radius_m"] = (500 + df["capacity_mw"].fillna(100).clip(0, 5000) * 0.5).round()
    df["provider"] = "wri"
    df["notes"] = df["primary_fuel"] + " " + df["capacity_mw"].round(0).astype(str) + " MW"
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    return gdf[["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes", "geometry"]]


def build_sites(include_osm: bool = True) -> gpd.GeoDataFrame:
    """Merge every provider into one point/polygon layer with a `site_type` column and a `radius_m` footprint."""
    parts = [load_seed_sites(), load_wri_power_plants()]
    try:
        from frims.ingest.gem import load_gem_sites
        gm = load_gem_sites()
        if len(gm):
            parts.append(gm)
    except Exception as e:  # noqa: BLE001
        log.warning("GEM trackers unavailable: %s", e)
    try:
        from frims.ingest.flares import load_flare_sites
        fl = load_flare_sites()
        if len(fl):
            parts.append(fl)
    except Exception as e:  # noqa: BLE001
        log.warning("flare catalogue unavailable: %s", e)
    if include_osm:
        try:
            from frims.ingest.osm_pbf import OSM_CACHE, PBF_PATH, load_pbf_sites
            if OSM_CACHE.exists() or PBF_PATH.exists():
                parts.append(load_pbf_sites())
            else:
                from frims.ingest.osm import load_osm_sites
                parts.append(load_osm_sites())
        except Exception as e:  # noqa: BLE001 — OSM is best-effort
            log.warning("OSM sites unavailable: %s", e)
    parts = [p for p in parts if len(p)]
    sites = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    sites["site_id"] = range(len(sites))
    sites["radius_m"] = sites["radius_m"].fillna(1000)
    return sites


def site_footprints(sites: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Polygons for spatial joins: real OSM polygons as-is, points buffered by radius_m (metres)."""
    metric = sites.to_crs("EPSG:7755")  # India-centric projected CRS (metres)
    geom = metric.geometry.copy()
    is_point = geom.geom_type == "Point"
    geom[is_point] = geom[is_point].buffer(metric.loc[is_point, "radius_m"])
    out = metric.set_geometry(geom).to_crs("EPSG:4326")
    return out
