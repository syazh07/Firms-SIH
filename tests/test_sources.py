import numpy as np
import pandas as pd

from frims.features.build import add_grid, add_rolling_persistence
from frims.features.sources import build_sources, flag_anomalies


def _detections():
    rows = []
    base = pd.Timestamp("2026-06-01")
    # persistent source: two adjacent cells, 40 nights each
    for d in range(40):
        for lat, lon in [(23.750, 86.420), (23.765, 86.435)]:
            rows.append(dict(latitude=lat, longitude=lon, acq_date=base + pd.Timedelta(days=d), is_night=True,
                             frp=10.0 + (d % 3), bt=330.0, label_rule="mining_thermal", near_site=True,
                             site_name="Jharia", site_type="coalfield", landcover="bare_sparse"))
    # single-day wildfire far away
    rows.append(dict(latitude=15.0, longitude=75.0, acq_date=base + pd.Timedelta(days=39), is_night=False, frp=3.0,
                     bt=320.0, label_rule="wildfire", near_site=False, site_name=None, site_type="none",
                     landcover="tree_cover"))
    # anomaly at the persistent source: huge FRP on the last day
    rows.append(dict(latitude=23.750, longitude=86.420, acq_date=base + pd.Timedelta(days=39), is_night=True,
                     frp=200.0, bt=360.0, label_rule="mining_thermal", near_site=True, site_name="Jharia",
                     site_type="coalfield", landcover="bare_sparse"))
    df = pd.DataFrame(rows)
    df["acq_datetime"] = df.acq_date
    return add_rolling_persistence(add_grid(df).sort_values("acq_datetime").reset_index(drop=True))


def test_adjacent_persistent_cells_form_one_source_and_anomaly_is_flagged():
    feats = _detections()
    sources, sid = build_sources(feats)
    assert len(sources) == 1
    src = sources.iloc[0]
    assert src.n_cells == 2 and src.label == "mining_thermal" and src.site_name == "Jharia"
    assert src.active_days_90 == 40
    out = flag_anomalies(feats, sources, sid)
    big = out[out.frp == 200.0].iloc[0]
    assert big.anomaly_score > 3 and big.label_rule == "industrial_fire"
    assert out.loc[out.landcover == "tree_cover", "source_id"].isna().all()


def test_rolling_persistence_uses_past_or_forward_warmup_window():
    feats = _detections()
    first = feats[feats.acq_date == feats.acq_date.min()].iloc[0]
    last = feats[(feats.acq_date == feats.acq_date.max()) & (feats.frp == 200.0)].iloc[0]
    # 40 days of data < 90-day window: the first row looks forward over the warm-up span, so both see all 40 days
    assert first.n_days_90 == 40 and last.n_days_90 == 40
    assert np.isclose(last.night_frac_90, 1.0)
    # with > 90 days of history, an early row must not see beyond its own 90-day span
    long = pd.concat([feats, feats.assign(acq_date=feats.acq_date + pd.Timedelta(days=120),
                                          acq_datetime=feats.acq_datetime + pd.Timedelta(days=120))], ignore_index=True)
    long = long.drop(columns=[c for c in long.columns if c.endswith("_30") or c.endswith("_90")])
    from frims.features.build import add_rolling_persistence
    long = add_rolling_persistence(long.sort_values("acq_datetime").reset_index(drop=True))
    jharia = long[long.latitude == 23.750]
    early = jharia[jharia.acq_date == jharia.acq_date.min()].iloc[0]
    late = jharia[jharia.acq_date == jharia.acq_date.max()].iloc[0]
    assert early.n_days_90 == 40 and late.n_days_90 == 40  # each block sees only its own 40-day burst
