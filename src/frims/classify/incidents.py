"""Event-level check: did FRIMS see (and flag) real, reported industrial incidents?

`data/incidents.csv` lists dated, source-verified incidents. For each we look at detections within `radius_km`
during [date-1 d, date+`days_after` d] in the dataset that covers the date, and report what the system saw:
detections, max FRP, labels, whether an alert (industrial_fire / anomaly) fired, the nearest known site, and the
30-day baseline before the event (to show the event is new, not a persistent source).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from frims import config

log = logging.getLogger(__name__)
INCIDENTS_CSV = config.DATA_DIR / "incidents.csv"
REPORT_PATH = config.MODELS_DIR / "incident_report.json"
KM_PER_DEG_LAT = 111.0


def _near(det: pd.DataFrame, lat: float, lon: float, km: float) -> pd.DataFrame:
    dlat = (det.latitude - lat) * KM_PER_DEG_LAT
    dlon = (det.longitude - lon) * KM_PER_DEG_LAT * np.cos(np.radians(lat))
    return det[np.sqrt(dlat ** 2 + dlon ** 2) <= km]


def check_incidents(datasets: dict[str, pd.DataFrame], csv: Path = INCIDENTS_CSV) -> dict:
    inc = pd.read_csv(csv)
    rows = []
    for _, r in inc.iterrows():
        date = pd.Timestamp(r.date)
        ds_name, det = None, None
        for name, d in datasets.items():
            if d.acq_date.min() <= date <= d.acq_date.max():
                ds_name, det = name, d
                break
        row = {"id": r.id, "name": r["name"], "date": r.date, "type": r.type, "expected_visible": r.expected_visible,
               "lat": r.lat, "lon": r.lon, "radius_km": r.radius_km, "source_url": r.source_url, "dataset": ds_name}
        if det is None:
            row.update(status="no_dataset"); rows.append(row); continue
        area = _near(det, r.lat, r.lon, r.radius_km)
        win = area[(area.acq_date >= date - pd.Timedelta(days=1)) & (area.acq_date <= date + pd.Timedelta(days=int(r.days_after)))]
        base = area[(area.acq_date >= date - pd.Timedelta(days=31)) & (area.acq_date < date - pd.Timedelta(days=1))]
        alert = win[(win.label_rule == "industrial_fire") | (win.get("anomaly_score", pd.Series(0, index=win.index)) >= 4)]
        watch = win[win.get("alert_tier", pd.Series("none", index=win.index)) == "watch"]
        site = win.site_name.dropna().mode()
        anom = win.get("anomaly_score", pd.Series(0.0, index=win.index))
        loc_conf = "high" if r.radius_km <= 10 else "low (wide search radius)"
        row.update(
            status="detected" if len(win) else "not_detected",
            location_confidence=loc_conf,
            anomaly_z_max=round(float(anom.max()), 2) if len(win) else None,
            industrial_share=round(float(win.label_rule.isin(["industrial_persistent", "gas_flare", "mining_thermal",
                                                                "industrial_fire"]).mean()), 2) if len(win) else None,
            n_detections=int(len(win)), n_days=int(win.acq_date.nunique()), frp_max=float(win.frp.max()) if len(win) else 0.0,
            labels=win.label_rule.value_counts().to_dict(), ml_industrial_share=round(float(win.label_ml.eq("industrial").mean()), 2) if len(win) else None,
            alert_fired=bool(len(alert)), n_alerts=int(len(alert)), watch_fired=bool(len(watch)), n_watch=int(len(watch)), nearest_site=site.iat[0] if len(site) else None,
            baseline_30d_days=int(base.acq_date.nunique()), baseline_30d_detections=int(len(base)),
            first_seen=win.acq_datetime.min().strftime("%Y-%m-%d %H:%M") if len(win) else None,
            sp_types=win.sp_type.dropna().astype(int).value_counts().to_dict() if "sp_type" in win and win.sp_type.notna().any() else None,
        )
        rows.append(row)
    checked = [x for x in rows if x["status"] != "no_dataset"]
    det_rows = [x for x in checked if x["status"] == "detected"]
    expected = [x for x in checked if x["expected_visible"] == "yes"]
    summary = {
        "n_incidents": len(rows), "n_with_data": len(checked), "n_detected": len(det_rows),
        "n_alert_fired": sum(1 for x in det_rows if x["alert_fired"]),
        "n_watch_fired": sum(1 for x in det_rows if x.get("watch_fired")),
        "expected_visible": {"n": len(expected), "detected": sum(1 for x in expected if x["status"] == "detected"),
                             "alerted": sum(1 for x in expected if x.get("alert_fired"))},
    }
    return {"summary": summary, "incidents": rows}


def main() -> None:
    import geopandas as gpd
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    datasets = {}
    for p in sorted(config.DATA_DIR.glob("frims_*.gpkg")):
        d = gpd.read_file(p, layer="detections")
        d["acq_date"] = pd.to_datetime(d.acq_date); d["acq_datetime"] = pd.to_datetime(d.acq_datetime)
        datasets[p.stem.replace("frims_", "")] = d
    rep = check_incidents(datasets)
    REPORT_PATH.write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep["summary"], indent=1))
    for x in rep["incidents"]:
        print(f"{x['date']} {x['name'][:42]:42s} [{x.get('dataset')}] {x['status']:13s} det={x.get('n_detections', '-')} "
              f"frp={x.get('frp_max', '-')} alert={x.get('alert_fired', '-')} labels={x.get('labels', '')}")


if __name__ == "__main__":
    main()
