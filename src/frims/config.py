"""Central settings for FRIMS. Thresholds live here so they can be tuned during a demo."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = Path(os.getenv("FRIMS_DATA_DIR", PROJECT_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
MODELS_DIR = PROJECT_ROOT / "models"
GPKG_PATH = DATA_DIR / "frims_live.gpkg"


def gpkg_path(dataset: str = "live") -> Path:
    return DATA_DIR / f"frims_{dataset}.gpkg"
SEED_SITES_CSV = DATA_DIR / "seed_sites.csv"
WEB_DIR = PROJECT_ROOT / "web"

MAP_KEY = os.getenv("MAP_KEY", "")

# FIRMS endpoints (from firms_api_use.ipynb)
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov"
FIRMS_STATUS_URL = f"{FIRMS_BASE}/mapserver/mapkey_status/?MAP_KEY={{key}}"
FIRMS_AREA_URL = f"{FIRMS_BASE}/api/area/csv/{{key}}/{{source}}/{{bbox}}/{{days}}"
FIRMS_AVAIL_URL = f"{FIRMS_BASE}/api/data_availability/csv/{{key}}/all"

# Region of interest: India (W,S,E,N)
INDIA_BBOX = (68.0, 6.0, 98.0, 36.0)
BBOX = tuple(float(x) for x in os.getenv("FRIMS_BBOX", ",".join(map(str, INDIA_BBOX))).split(","))

SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"]
SOURCE_SETS = {"nrt": SOURCES, "sp": ["VIIRS_NOAA20_SP", "VIIRS_SNPP_SP"]}

# Rate limit handling
TX_LIMIT_DEFAULT = 5000
TX_SAFETY_MARGIN = 500
TX_DEFAULT_ESTIMATE = 100          # assumed cost of an area call before we have observed one
TX_STATUS_EVERY_N_CALLS = 3
FIRMS_MAX_DAYS_PER_CALL = 5          # API rejects >5 for NRT sources (tutorial's 10 is outdated)
FIRMS_SLEEP_BETWEEN_CALLS_S = 2.0
FIRMS_REFRESH_MINUTES = 30         # today's window is only re-fetched if cache is older than this
REFRESH_DEBOUNCE_MINUTES = 15      # /api/refresh debounce

# Feature engineering
GRID_DEG = 0.01                    # ~1.1 km cells
PERSISTENCE_WINDOWS_DAYS = (30, 90)
SITE_MATCH_DISTANCE_M = 1500.0


@dataclass(frozen=True)
class RuleThresholds:
    persistent_min_days_90: int = 10
    # a large site footprint (15 km coalfield) must not vouch for a cell that is not itself recurring:
    mining_min_cell_days_90: int = 10          # mining_thermal needs the cell (not just the site) persistent
    near_site_min_cell_days_90: int = 5        # persistent-near-site (outside the polygon) needs some cell recurrence
    flare_min_night_fraction: float = 0.5
    agri_max_days: int = 3
    industrial_fire_frp_multiplier: float = 2.0
    offshore_min_distance_km: float = 5.0
    flare_site_types: tuple[str, ...] = ("refinery", "oil_gas", "lng", "petrochemical", "offshore_platform", "flare_site")
    catalogued_flare_types: tuple[str, ...] = ("flare_site",)   # from a flare survey: flaring even when intermittent
    mining_site_types: tuple[str, ...] = ("coal_mine", "coalfield", "mine", "quarry")
    # OSM landuse=industrial / man_made=works / quarry polygons are numerous and loosely mapped: they never confer
    # site-level persistence and a one-off detection near them is not an industrial-fire alert.
    generic_site_types: tuple[str, ...] = ("industrial_area", "works", "quarry")


RULES = RuleThresholds()

CLASSES = [
    "industrial_persistent",
    "gas_flare",
    "mining_thermal",
    "industrial_fire",
    "agricultural_burn",
    "wildfire",
    "volcano",
    "offshore_other",
]
VOLCANO_MATCH_M = 6000.0
ML_VETO_P = 0.3   # rule 'industrial' is demoted when the truth-trained model gives p(industrial) below this
REFRESH_EVERY_HOURS = float(os.getenv("FRIMS_REFRESH_HOURS", "3"))   # in-process scheduler; 0 disables

# ESA WorldCover 2021 v200 (public COGs on AWS, 3x3 degree tiles)
WORLDCOVER_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif"
)
WORLDCOVER_CLASSES = {
    10: "tree_cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built_up",
    60: "bare_sparse", 70: "snow_ice", 80: "water", 90: "wetland", 95: "mangroves", 100: "moss_lichen",
    0: "unknown",
}

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass.kumi.systems/api/interpreter")  # overpass-api.de often unreachable
OVERPASS_TILE_DEG = 5.0

WRI_POWER_PLANTS_URL = (
    "https://raw.githubusercontent.com/wri/global-power-plant-database/master/output_database/global_power_plant_database.csv"
)


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, RAW_DIR / "firms", CACHE_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
