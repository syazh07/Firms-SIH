"""ESA WorldCover 2021 land cover per grid cell.

Instead of point-sampling the 10 m COG over HTTP (thousands of scattered block reads), each 3°x3° tile is read once
at a ~640 m overview (a few hundred KB) and every grid cell in that tile is looked up in memory. Results are cached
per grid cell so repeat runs are free.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

from frims import config

log = logging.getLogger(__name__)
OVERVIEW_FACTOR = 64  # 10 m * 64 = 640 m, below the 0.01 deg (~1.1 km) grid


def _tile_origin(lat: float, lon: float) -> tuple[int, int]:
    return int(math.floor(lat / 3.0) * 3), int(math.floor(lon / 3.0) * 3)


def _tile_url(lat0: int, lon0: int) -> str:
    lat_s = f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}"
    lon_s = f"{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"
    return config.WORLDCOVER_URL.format(lat=lat_s, lon=lon_s)


def _read_tile_overview(url: str):
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(f"/vsicurl/{url}") as ds:
            h, w = ds.height // OVERVIEW_FACTOR, ds.width // OVERVIEW_FACTOR
            arr = ds.read(1, out_shape=(h, w), resampling=Resampling.nearest)
            transform = ds.transform * ds.transform.scale(ds.width / w, ds.height / h)
    return arr, transform


def sample_landcover(cells: pd.DataFrame, cache: Path = config.CACHE_DIR / "landcover_cells.parquet",
                     online: bool = True) -> pd.DataFrame:
    """cells: DataFrame with grid_id, cell_lat, cell_lon -> DataFrame grid_id, landcover_code, landcover."""
    cells = cells.drop_duplicates("grid_id")[["grid_id", "cell_lat", "cell_lon"]]
    cached = pd.read_parquet(cache) if cache.exists() else pd.DataFrame({"grid_id": pd.Series(dtype=str),
                                                                          "landcover_code": pd.Series(dtype=int)})
    todo = cells[~cells.grid_id.isin(cached.grid_id)]
    new_rows = []
    if len(todo) and online:
        todo = todo.assign(tile=[_tile_origin(la, lo) for la, lo in zip(todo.cell_lat, todo.cell_lon)])
        for (lat0, lon0), grp in todo.groupby("tile"):
            url = _tile_url(lat0, lon0)
            try:
                arr, transform = _read_tile_overview(url)
                inv = ~transform
                cols, rows = inv * (grp.cell_lon.values, grp.cell_lat.values)
                r = np.clip(rows.astype(int), 0, arr.shape[0] - 1)
                c = np.clip(cols.astype(int), 0, arr.shape[1] - 1)
                codes = arr[r, c].astype(int)
                log.info("WorldCover %s -> %d cells", url.rsplit('/', 1)[-1], len(grp))
            except Exception as e:  # noqa: BLE001 — sea tiles do not exist; offline
                log.warning("WorldCover tile %s unavailable (%s); marking %d cells unknown",
                            url.rsplit('/', 1)[-1], str(e)[:80], len(grp))
                codes = np.zeros(len(grp), dtype=int)
            new_rows.append(pd.DataFrame({"grid_id": grp.grid_id.values, "landcover_code": codes}))
    if new_rows:
        cached = pd.concat([cached] + new_rows, ignore_index=True).drop_duplicates("grid_id")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cached.to_parquet(cache, index=False)
    out = cells[["grid_id"]].merge(cached, on="grid_id", how="left")
    out["landcover_code"] = out["landcover_code"].fillna(0).astype(int)
    out["landcover"] = out["landcover_code"].map(config.WORLDCOVER_CLASSES).fillna("unknown")
    return out
