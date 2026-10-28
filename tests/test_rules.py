import pandas as pd

from frims.classify.rules import classify_rules


def _row(**kw):
    base = dict(landcover="cropland", site_type="none", near_site=False, in_site=False, n_days_90=1,
                night_frac_90=0.0, frp_ratio=1.0, is_night=False)
    base.update(kw)
    return base


def test_each_rule_fires_in_priority_order():
    df = pd.DataFrame([
        _row(landcover="water"),                                                                # offshore
        _row(site_type="refinery", near_site=True, n_days_90=40, night_frac_90=0.9),            # flare
        _row(site_type="coalfield", near_site=True, n_days_90=20, days_90_eff=80),              # mining
        _row(site_type="steel", near_site=True, n_days_90=25),                                  # persistent
        _row(site_type="power_plant", near_site=True, in_site=True, n_days_90=1),               # industrial fire
        _row(landcover="cropland", n_days_90=1),                                                # agri burn
        _row(landcover="tree_cover", n_days_90=1),                                              # wildfire
        _row(landcover="built_up", n_days_90=30),                                               # unknown persistent
        _row(landcover="cropland", n_days_90=40, night_frac_90=0.9),                            # kiln-like, not agri
    ])
    assert classify_rules(df).tolist() == [
        "offshore_other", "gas_flare", "mining_thermal", "industrial_persistent", "industrial_fire",
        "agricultural_burn", "wildfire", "industrial_persistent", "industrial_persistent",
    ]


def test_refinery_that_only_burns_by_day_is_not_a_flare():
    df = pd.DataFrame([_row(site_type="refinery", near_site=True, n_days_90=30, night_frac_90=0.1)])
    assert classify_rules(df).iloc[0] == "industrial_persistent"


def test_offshore_platform_on_water_is_flare_not_offshore_other():
    df = pd.DataFrame([_row(landcover="water", site_type="offshore_platform", near_site=True, n_days_90=20,
                            night_frac_90=0.8)])
    assert classify_rules(df).iloc[0] == "gas_flare"


def test_site_level_persistence_promotes_cell_with_few_days():
    df = pd.DataFrame([_row(site_type="refinery", near_site=True, n_days_90=2, days_90_eff=45, night_frac_90=0.9)])
    assert classify_rules(df).iloc[0] == "gas_flare"


def test_power_plant_on_reservoir_is_not_offshore():
    df = pd.DataFrame([_row(landcover="water", site_type="power_plant", near_site=True, n_days_90=30)])
    assert classify_rules(df).iloc[0] == "industrial_persistent"


def test_generic_osm_area_does_not_raise_alerts_or_mining_without_persistence():
    df = pd.DataFrame([
        _row(site_type="industrial_area", near_site=True, in_site=True, n_days_90=1, landcover="cropland"),
        _row(site_type="quarry", near_site=True, in_site=True, n_days_90=2, landcover="tree_cover"),
        _row(site_type="industrial_area", near_site=True, in_site=True, n_days_90=30),
    ])
    assert classify_rules(df).tolist() == ["agricultural_burn", "wildfire", "industrial_persistent"]


def test_fallback_by_landcover_and_ml_veto_combination():
    from frims.classify.rules import fallback_by_landcover
    from frims.classify.model import _combine
    assert fallback_by_landcover(pd.Series(["cropland", "tree_cover", "water", None])).tolist() == \
        ["agricultural_burn", "wildfire", "offshore_other", "offshore_other"]
    df = pd.DataFrame({"label_rule": ["mining_thermal", "mining_thermal", "industrial_fire", "wildfire", "gas_flare"],
                       "proba_industrial": [0.05, 0.9, 0.05, 0.05, 0.05], "landcover": ["cropland"] * 5,
                       "site_type": ["coal_mine", "coal_mine", "steel", "none", "flare_site"]})
    out = _combine(df, veto_p=0.3)
    assert out.label_final.tolist() == ["agricultural_burn", "mining_thermal", "industrial_fire", "wildfire", "gas_flare"]
    assert out.ml_veto.tolist() == [True, False, False, False, False]


def test_catalogued_flare_is_gas_flare_even_when_intermittent():
    df = pd.DataFrame([
        _row(site_type="flare_site", near_site=True, in_site=True, n_days_90=2, night_frac_90=1.0),   # lit 2 nights
        _row(site_type="oil_gas", near_site=True, in_site=True, n_days_90=2, night_frac_90=1.0),      # seed field, not catalogued flare
    ])
    assert classify_rules(df).tolist() == ["gas_flare", "industrial_fire"]


def test_large_site_footprint_does_not_vouch_for_a_non_recurring_cell():
    df = pd.DataFrame([
        _row(site_type="coalfield", near_site=True, in_site=True, n_days_90=2, days_90_eff=85, landcover="cropland"),   # farm fire inside 15 km coalfield radius
        _row(site_type="coalfield", near_site=True, in_site=True, n_days_90=40, days_90_eff=85, landcover="bare_sparse"),  # real coal fire cell
        _row(site_type="steel", near_site=True, in_site=False, n_days_90=1, days_90_eff=30, landcover="cropland"),       # 1 km outside a steel plant, seen once
        _row(site_type="steel", near_site=True, in_site=True, n_days_90=1, days_90_eff=30, landcover="built_up"),       # inside the plant, seen once (site vouches)
    ])
    assert classify_rules(df).tolist() == ["agricultural_burn", "mining_thermal", "agricultural_burn", "industrial_persistent"]
