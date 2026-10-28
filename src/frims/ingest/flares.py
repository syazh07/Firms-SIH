"""Gas-flare sites from the EOG / NASA ORNL DAAC 'Global Gas Flare Survey by Infrared Imaging, VIIRS Nightfire'
(DOI 10.3334/ORNLDAAC/1874; open licence, Earthdata sign-in to download).

Drop `eog_global_flare_survey_<year>_flare_list.csv` files into data/raw/flares/. Every flare inside the bbox becomes
a site of type `oil_gas` (upstream) or `refinery`/`lng` when the survey's type column says so, with a 1 km footprint. Site type `flare_site`: the rules label any detection there `gas_flare` without a
persistence requirement (intermittent flaring is still flaring); FRP anomalies still raise alerts.
The loader is tolerant of column naming: it finds latitude/longitude/country/type columns case-insensitively.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from frims import config

log = logging.getLogger(__name__)
FLARE_DIR = config.RAW_DIR / "flares"
TYPE_MAP = {"upstream": "flare_site", "downstream": "flare_site", "lng": "flare_site", "midstream": "flare_site"}


def _col(df: pd.DataFrame, *cands: str) -> str | None:
    low = {c.lower(): c for c in df.columns}
    for c in cands:
        for k, orig in low.items():
            if c in k:
                return orig
    return None


def load_flare_sites(flare_dir: Path = FLARE_DIR, bbox=config.BBOX) -> gpd.GeoDataFrame:
    files = sorted(flare_dir.rglob("*flare_list*.csv"))
    empty = gpd.GeoDataFrame(columns=["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes"],
                             geometry=[], crs="EPSG:4326")
    if not files:
        return empty
    frames = []
    for f in files:
        df = pd.read_csv(f, low_memory=False)
        lat, lon = _col(df, "latitude", "lat"), _col(df, "longitude", "lon")
        if not lat or not lon:
            log.warning("%s: no lat/lon columns (%s)", f.name, list(df.columns)[:8]); continue
        year = "".join(ch for ch in f.stem if ch.isdigit())[-4:]
        typ = _col(df, "type", "category", "sector")
        cty = _col(df, "cntry_name", "country")
        cat = _col(df, "catalog_id")
        out = pd.DataFrame({"latitude": pd.to_numeric(df[lat], errors="coerce"), "longitude": pd.to_numeric(df[lon], errors="coerce")})
        out["site_type"] = "flare_site"  # the survey is a catalogue of flares: any detection there is flaring
        out["flare_kind"] = df[typ].astype(str).str.lower() if typ else "unknown"
        out["name"] = "Nightfire flare " + (df[cat].astype(str) if cat else year)
        out["notes"] = ("EOG VIIRS Nightfire survey " + year + " · " + out["flare_kind"] + (" · " + df[cty].astype(str) if cty else "")
                        + (" · avg " + df[_col(df, "avg_temp")].round(0).astype(str) + " K" if _col(df, "avg_temp") else ""))
        frames.append(out.dropna(subset=["latitude", "longitude"]))
    if not frames:
        return empty
    df = pd.concat(frames, ignore_index=True)
    w, s, e, n = bbox
    df = df[df.longitude.between(w, e) & df.latitude.between(s, n)]
    # the same flare appears in every yearly survey: keep one per ~500 m
    df["k"] = (df.latitude.round(3).astype(str) + "_" + df.longitude.round(3).astype(str))
    df = df.drop_duplicates("k").drop(columns="k")
    df["radius_m"] = 1000.0
    df["provider"] = "nightfire"
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    log.info("Nightfire flares in bbox: %d (%s)", len(gdf), gdf.site_type.value_counts().to_dict())
    return gdf[["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes", "geometry"]]
