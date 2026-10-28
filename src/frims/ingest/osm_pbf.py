"""Industrial infrastructure from a Geofabrik .osm.pbf extract (no Overpass, no osmium-tool needed).

pyosmium's FileProcessor streams the file; `with_areas()` assembles closed ways / multipolygon relations into
polygons. Two passes: an explicit tag list, then anything carrying an `industrial=*` key.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkb

from frims import config
from frims.ingest.osm import classify_tags

log = logging.getLogger(__name__)
PBF_PATH = config.RAW_DIR / "osm" / "india-latest.osm.pbf"
OSM_CACHE = config.CACHE_DIR / "osm_sites.gpkg"

TAGS = [("landuse", "industrial"), ("landuse", "quarry"), ("landuse", "mine"), ("man_made", "works"),
        ("man_made", "flare"), ("man_made", "petroleum_well"), ("man_made", "oil_refinery"), ("power", "plant"),
        ("resource", "coal")]
POWER_SOURCES = ("coal", "gas", "oil", "biomass", "waste", "diesel", "lignite")


def _wanted(tags: dict) -> bool:
    if tags.get("power") == "plant":
        src = tags.get("plant:source", "")
        return any(s in src for s in POWER_SOURCES) or src == ""
    return True


def _iter_features(pbf: Path):
    import osmium
    from osmium.filter import KeyFilter, TagFilter
    fab = osmium.geom.WKBFactory()
    seen: set[str] = set()
    for flt in (TagFilter(*TAGS), KeyFilter("industrial")):
        # NOTE: ways/relations must stay in the entity mask or the area assembler never sees them (yields no polygons)
        fp = osmium.FileProcessor(str(pbf)).with_areas().with_filter(flt)
        for obj in fp:
            tags = dict(obj.tags)
            if not _wanted(tags):
                continue
            if obj.is_area():
                oid = f"{'relation' if obj.from_way() is False else 'way'}/{obj.orig_id()}"
                try:
                    geom = wkb.loads(bytes.fromhex(fab.create_multipolygon(obj)))
                except Exception:  # noqa: BLE001 — broken multipolygon
                    continue
            elif obj.is_node():
                oid = f"node/{obj.id}"
                geom = wkb.loads(bytes.fromhex(fab.create_point(obj)))
            else:
                continue
            if oid in seen:
                continue
            seen.add(oid)
            yield {"osm_id": oid, "name": tags.get("name", ""), "site_type": classify_tags(tags), "geometry": geom}


def load_pbf_sites(pbf: Path = PBF_PATH, cache: Path = OSM_CACHE, refresh: bool = False) -> gpd.GeoDataFrame:
    if cache.exists() and not refresh:
        return gpd.read_file(cache)
    if not pbf.exists():
        raise FileNotFoundError(f"{pbf} missing — download https://download.geofabrik.de/asia/india-latest.osm.pbf")
    rows = list(_iter_features(pbf))
    if not rows:
        raise RuntimeError("no industrial features found in PBF")
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    cen = gdf.geometry.to_crs("EPSG:7755").centroid.to_crs("EPSG:4326")
    gdf["latitude"], gdf["longitude"] = cen.y, cen.x
    gdf["radius_m"] = 300.0  # only used for point features; polygons keep their real footprint
    gdf["provider"] = "osm"
    gdf["notes"] = gdf["osm_id"]
    gdf = gdf[["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes", "geometry"]]
    cache.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(cache, driver="GPKG")
    log.info("OSM PBF -> %d industrial features (%s)", len(gdf), gdf.site_type.value_counts().to_dict())
    return gdf
