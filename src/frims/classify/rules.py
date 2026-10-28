"""Deterministic, ordered classification rules (first match wins). See plan table for rationale."""
from __future__ import annotations

import numpy as np
import pandas as pd

from frims import config

R = config.RULES

VEG_CLASSES = ("tree_cover", "shrubland", "grassland", "wetland", "mangroves")


def classify_rules(df: pd.DataFrame) -> pd.Series:
    idx = df.index
    lc = df.get("landcover", pd.Series("unknown", index=idx)).fillna("unknown")
    st = df.get("site_type", pd.Series("none", index=idx)).fillna("none")
    n90 = df.get("n_days_90", pd.Series(1, index=idx)).fillna(1)
    n90_eff = df["days_90_eff"].fillna(n90) if "days_90_eff" in df else n90
    n90_eff = np.maximum(n90_eff, n90)
    night = df.get("night_frac_90", pd.Series(0.0, index=idx)).fillna(0)
    near = df.get("near_site", pd.Series(False, index=idx)).fillna(False).astype(bool)
    in_site = df.get("in_site", near).fillna(False).astype(bool)
    is_day = ~df.get("is_night", pd.Series(False, index=idx)).astype(bool)

    persistent = n90_eff >= R.persistent_min_days_90
    specific = ~st.isin(R.generic_site_types)
    cell_mining_ok = n90 >= R.mining_min_cell_days_90
    cell_near_ok = in_site | (n90 >= R.near_site_min_cell_days_90)
    # a mining-site detection whose own cell is not recurring gets no industrial label from the site at all
    mining_fail = st.isin(R.mining_site_types) & ~cell_mining_ok
    cell_persistent = n90 >= R.persistent_min_days_90

    conds = [
        near & st.eq("volcano"),
        near & st.isin(R.catalogued_flare_types),        # catalogued flare: flaring whether or not persistent
        near & st.isin(R.flare_site_types) & (night >= R.flare_min_night_fraction) & persistent,
        near & st.isin(R.mining_site_types) & persistent & cell_mining_ok,
        near & persistent & cell_near_ok & ~mining_fail,
        in_site & specific & ~mining_fail,              # one-off event inside a named industrial site: alert
        cell_persistent,                                # recurring cell with no (vouching) site: unknown persistent source
        lc.eq("water"),
        lc.eq("cropland") & is_day & (n90 <= R.agri_max_days),
        lc.isin(VEG_CLASSES),
        lc.eq("cropland"),
    ]
    labels = [
        "volcano", "gas_flare", "gas_flare", "mining_thermal", "industrial_persistent", "industrial_fire", "industrial_persistent",
        "offshore_other", "agricultural_burn", "wildfire", "agricultural_burn",
    ]
    return pd.Series(np.select(conds, labels, default="offshore_other"), index=idx, name="label_rule")


def explain(row: pd.Series) -> str:
    bits = []
    if row.get("site_type", "none") != "none":
        bits.append(f"{row['site_type']} site {row.get('site_name') or ''} at {row.get('dist_site_m', 0):.0f} m")
    bits.append(f"{int(row.get('n_days_90', 0))} active days / 90d in cell"
                + (f", {int(row['site_days_90'])} at site" if row.get('site_days_90', 0) else ""))
    bits.append(f"night fraction {row.get('night_frac_90', 0):.2f}")
    bits.append(f"land cover {row.get('landcover', 'unknown')}")
    return "; ".join(bits)


def fallback_by_landcover(lc: pd.Series) -> pd.Series:
    """Label a detection by land cover alone (used when the ML veto removes a rule-based industrial label)."""
    lc = lc.fillna("unknown")
    return pd.Series(np.select([lc.eq("cropland"), lc.isin(VEG_CLASSES)], ["agricultural_burn", "wildfire"], "offshore_other"),
                     index=lc.index)
