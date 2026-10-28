import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from frims.features.build import add_grid, join_sites, persistence_stats
from frims.ingest.firms import normalise


def _det(lat, lon, day, night=False, frp=5.0):
    return dict(latitude=lat, longitude=lon, acq_date=pd.Timestamp("2026-08-01") + pd.Timedelta(days=day),
                is_night=night, frp=frp, bt=330.0)


def test_grid_and_persistence_counts_unique_days():
    df = add_grid(pd.DataFrame([_det(22.3506, 69.86, 0), _det(22.3507, 69.8601, 0), _det(22.3506, 69.86, 5, True),
                                _det(10.0, 78.0, 5)]))
    assert df.grid_id.nunique() == 2
    st = persistence_stats(df, ref_date=pd.Timestamp("2026-08-10"))
    jam = st[st.grid_id == df.grid_id.iloc[0]].iloc[0]
    assert jam.n_days_90 == 2 and jam.n_det_90 == 3
    assert abs(jam.night_frac_90 - 1 / 3) < 1e-9


def test_join_sites_flags_detection_inside_footprint():
    sites = gpd.GeoDataFrame({"site_id": [0], "name": ["Jamnagar"], "site_type": ["refinery"], "radius_m": [6000.0]},
                             geometry=[Point(69.86, 22.3506)], crs="EPSG:4326")
    df = add_grid(pd.DataFrame([_det(22.36, 69.87, 0), _det(15.0, 75.0, 0)]))
    out = join_sites(df, sites)
    assert out.in_site.tolist() == [True, False]
    assert out.site_type.tolist() == ["refinery", "none"]


def test_normalise_maps_viirs_letter_confidence_and_modis_numbers():
    v = pd.DataFrame(dict(latitude=[1.0], longitude=[2.0], bright_ti4=[330.0], bright_ti5=[290.0], scan=[.4], track=[.4],
                          acq_date=["2026-08-01"], acq_time=[622], satellite=["N20"], instrument=["VIIRS"],
                          confidence=["h"], version=["2.0NRT"], frp=[3.0], daynight=["N"]))
    m = pd.DataFrame(dict(latitude=[1.0], longitude=[2.0], brightness=[330.0], bright_t31=[290.0], scan=[1], track=[1],
                          acq_date=["2026-08-01"], acq_time=[1425], satellite=["Aqua"], instrument=["MODIS"],
                          confidence=[83], version=["6.1NRT"], frp=[3.0], daynight=["D"]))
    nv, nm = normalise(v, "VIIRS_NOAA20_NRT"), normalise(m, "MODIS_NRT")
    assert nv.conf_norm.iloc[0] == 0.9 and nm.conf_norm.iloc[0] == 0.83
    assert nv.is_night.iloc[0] and not nm.is_night.iloc[0]
    assert str(nv.acq_datetime.iloc[0]) == "2026-08-01 06:22:00"
    assert set(nv.columns) == set(nm.columns)


def test_join_prefers_seed_complex_over_catalogued_flare_point_inside_it():
    sites = gpd.GeoDataFrame({"site_id": [0, 1], "name": ["Jamnagar complex", "Nightfire flare X"],
                              "site_type": ["refinery", "flare_site"], "radius_m": [6000.0, 1000.0],
                              "provider": ["seed", "nightfire"]},
                             geometry=[Point(69.86, 22.3506), Point(69.861, 22.351)], crs="EPSG:4326")
    df = add_grid(pd.DataFrame([_det(22.3508, 69.8605, 0)]))
    out = join_sites(df, sites)
    assert out.site_name.iloc[0] == "Jamnagar complex" and out.site_type.iloc[0] == "refinery"
