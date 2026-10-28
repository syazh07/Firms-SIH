"""Global Energy Monitor trackers (Global Coal Mine Tracker; optionally steel / oil & gas / LNG trackers).

GEM data is free (CC BY 4.0) but downloaded via a form at https://globalenergymonitor.org/projects/global-coal-mine-tracker/download-data/.
Drop the .xlsx/.csv files into data/raw/gem/. Column names differ between releases; we detect
latitude/longitude/name/status/type columns case-insensitively and keep operating mines inside the bbox.
Mines get site_type `coal_mine` and a footprint = equivalent-circle radius of the reported mine area (1–8 km,
1.5 km when unknown); the rules still require the detection's own cell to be recurring.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from frims import config

log = logging.getLogger(__name__)
GEM_DIR = config.RAW_DIR / "gem"
_ACTIVE = ("operating", "active", "producing", "operational", "mothballed")  # mothballed pits still smoulder


def _col(df: pd.DataFrame, *cands: str) -> str | None:
    low = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        for k, orig in low.items():
            if k == c or c in k:
                return orig
    return None


def _read_any(f: Path) -> list[pd.DataFrame]:
    if f.suffix.lower() in (".xlsx", ".xls"):
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unknown extension")  # openpyxl on GEM's conditional formatting
            sheets = pd.read_excel(f, sheet_name=None)
        return [d for d in sheets.values() if len(d) > 10 and _col(d, "latitude", "lat")]
    return [pd.read_csv(f, low_memory=False)]


def load_gem_sites(gem_dir: Path = GEM_DIR, bbox=config.BBOX) -> gpd.GeoDataFrame:
    empty = gpd.GeoDataFrame(columns=["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes"],
                             geometry=[], crs="EPSG:4326")
    files = sorted(list(gem_dir.glob("*.xlsx")) + list(gem_dir.glob("*.xls")) + list(gem_dir.glob("*.csv")))
    if not files:
        return empty
    frames = []
    for f in files:
        for df in _read_any(f):
            lat, lon = _col(df, "latitude", "lat"), _col(df, "longitude", "lon")
            if not lat or not lon:
                continue
            name = _col(df, "mine name", "plant name", "project name", "unit name", "name")
            status = _col(df, "status")
            mtype = _col(df, "mine type", "mining method", "type")
            area = _col(df, "mine size")
            gid = _col(df, "gem mine id", "gem id")
            out = pd.DataFrame({"latitude": pd.to_numeric(df[lat], errors="coerce"),
                                "longitude": pd.to_numeric(df[lon], errors="coerce"),
                                "name": (df[name].astype(str) if name else f.stem) + (" (" + df[gid].astype(str) + ")" if gid else "")})
            km2 = pd.to_numeric(df[area], errors="coerce") if area else pd.Series(float("nan"), index=df.index)
            out["radius_m"] = (km2 * 1e6 / 3.14159) ** 0.5   # equivalent-circle radius of the reported mine area
            st = df[status].astype(str).str.lower() if status else pd.Series("operating", index=df.index)
            out = out[st.str.contains("|".join(_ACTIVE), na=False)]
            kind = df[mtype].astype(str).str.lower() if mtype else pd.Series("", index=df.index)
            out["site_type"] = "coal_mine" if "coal" in f.stem.lower() else "industrial_area"
            out["notes"] = "GEM " + f.stem + (" · " + kind.reindex(out.index).fillna("") if mtype else "")
            frames.append(out.dropna(subset=["latitude", "longitude"]))
    if not frames:
        log.warning("GEM files present but no lat/lon columns recognised")
        return empty
    df = pd.concat(frames, ignore_index=True)
    w, s, e, n = bbox
    df = df[df.longitude.between(w, e) & df.latitude.between(s, n)].drop_duplicates(["latitude", "longitude"])
    df["radius_m"] = df["radius_m"].fillna(1500.0).clip(lower=1000.0, upper=8000.0)
    df["provider"] = "gem"
    gdf = gpd.GeoDataFrame(df, geometry=[Point(xy) for xy in zip(df.longitude, df.latitude)], crs="EPSG:4326")
    log.info("GEM sites in bbox: %d (%s)", len(gdf), gdf.site_type.value_counts().to_dict())
    return gdf[["name", "site_type", "latitude", "longitude", "radius_m", "provider", "notes", "geometry"]]
