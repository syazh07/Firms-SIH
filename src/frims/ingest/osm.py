"""OSM industrial infrastructure via Overpass, fetched in tiles and cached to GeoPackage."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point, Polygon

from frims import config

log = logging.getLogger(__name__)

# tag -> site_type
TAG_QUERIES = [
    ('way["landuse"="industrial"]', "industrial_area"),
    ('relation["landuse"="industrial"]', "industrial_area"),
    ('way["landuse"="quarry"]', "quarry"),
    ('nwr["man_made"="works"]', "works"),
    ('nwr["industrial"~"refinery|oil|gas|petrochemical|steel|chemical"]', "industrial_tagged"),
    ('nwr["man_made"="flare"]', "refinery"),
    ('nwr["power"="plant"]["plant:source"~"coal|gas|oil|biomass|waste"]', "power_plant"),
    ('nwr["man_made"="petroleum_well"]', "oil_gas"),
    ('nwr["resource"~"coal"]', "coal_mine"),
    ('nwr["landuse"="mine"]', "mine"),  # deprecated tag but still present in India
]

_INDUSTRIAL_REFINE = {"refinery": "refinery", "oil": "oil_gas", "gas": "oil_gas", "petrochemical": "petrochemical",
                      "steel": "steel", "chemical": "chemical"}


def _tiles(bbox, step):
    w, s, e, n = bbox
    lon = w
    while lon < e:
        lat = s
        while lat < n:
            yield (lon, lat, min(lon + step, e), min(lat + step, n))
            lat += step
        lon += step


def _overpass(query: str, retries: int = 3) -> dict:
    delay = 10
    for attempt in range(retries):
        try:
            r = requests.post(config.OVERPASS_URL, data={"data": query}, timeout=(20, 400))
        except requests.RequestException as e:
            log.warning("Overpass network error (attempt %d): %s", attempt + 1, e)
            time.sleep(delay); delay *= 2
            continue
        if r.status_code == 200:
            return r.json()
        log.warning("Overpass %s (attempt %d): %s", r.status_code, attempt + 1, r.text[:100])
        if r.status_code in (400, 406):  # query error — retrying won't help
            raise RuntimeError(f"Overpass rejected query: {r.text[:200]}")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("Overpass failed")


def _element_geom(el: dict):
    if el["type"] == "node":
        return Point(el["lon"], el["lat"])
    if "geometry" in el and len(el["geometry"]) >= 4:
        pts = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if pts[0] == pts[-1]:
            try:
                poly = Polygon(pts)
                if poly.is_valid and poly.area > 0:
                    return poly
            except Exception:  # noqa: BLE001
                pass
    if "center" in el:
        return Point(el["center"]["lon"], el["center"]["lat"])
    if "bounds" in el:
        b = el["bounds"]
        return Point((b["minlon"] + b["maxlon"]) / 2, (b["minlat"] + b["maxlat"]) / 2)
    return None


def fetch_tile(tile) -> list[dict]:
    w, s, e, n = tile
    bbox = f"{s},{w},{n},{e}"
    body = "".join(f"{q}({bbox});" for q, _ in TAG_QUERIES)
    query = f"[out:json][timeout:300];({body});out tags geom;"
    data = _overpass(query)
    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        geom = _element_geom(el)
        if geom is None:
            continue
        site_type = classify_tags(tags)
        rows.append({"osm_id": f'{el["type"]}/{el["id"]}', "name": tags.get("name", ""), "site_type": site_type,
                     "geometry": geom})
    return rows


def classify_tags(tags: dict) -> str:
    ind = tags.get("industrial", "")
    for k, v in _INDUSTRIAL_REFINE.items():
        if k in ind:
            return v
    if tags.get("man_made") == "flare" or "refinery" in tags.get("name", "").lower():
        return "refinery"
    if tags.get("man_made") == "petroleum_well":
        return "oil_gas"
    if tags.get("power") == "plant":
        return "power_plant"
    if "coal" in tags.get("resource", "") or tags.get("landuse") == "mine":
        return "coal_mine"
    if tags.get("landuse") == "quarry":
        return "quarry"
    if tags.get("man_made") == "works":
        return "works"
    return "industrial_area"


def load_osm_sites(bbox=config.BBOX, cache: Path = config.CACHE_DIR / "osm_sites.gpkg",
                   refresh: bool = False) -> gpd.GeoDataFrame:
    if cache.exists() and not refresh:
        return gpd.read_file(cache)
    all_rows: list[dict] = []
    tiles = list(_tiles(bbox, config.OVERPASS_TILE_DEG))
    for i, tile in enumerate(tiles, 1):
        try:
            rows = fetch_tile(tile)
            log.info("Overpass tile %d/%d %s -> %d features", i, len(tiles), tile, len(rows))
            all_rows.extend(rows)
        except Exception as e:  # noqa: BLE001
            log.warning("Overpass tile %s failed: %s", tile, e)
        time.sleep(3)
    if not all_rows:
        raise RuntimeError("no OSM features fetched")
    gdf = gpd.GeoDataFrame(all_rows, geometry="geometry", crs="EPSG:4326")
    gdf = gdf.drop_duplicates(subset=["osm_id"])
    gdf["latitude"] = gdf.geometry.centroid.y
    gdf["longitude"] = gdf.geometry.centroid.x
    gdf["radius_m"] = 500.0
    gdf["provider"] = "osm"
    gdf["notes"] = gdf["osm_id"]
    gdf = gdf[["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes", "geometry"]]
    cache.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache, driver="GPKG")
    return gdf
