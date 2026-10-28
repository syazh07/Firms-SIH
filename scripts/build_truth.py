"""Pull FIRMS standard-processing (SP) detections that carry the `type` attribute -> ground truth for evaluation.

type: 0 = presumed vegetation fire, 1 = active volcano, 2 = other static land source, 3 = offshore.
Completed windows are cached by FirmsClient, so re-running costs 0 transactions.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from frims import config  # noqa: E402
from frims.ingest.firms import FirmsClient  # noqa: E402

log = logging.getLogger("truth")
TRUTH_DIR = config.DATA_DIR / "truth"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="VIIRS_NOAA20_SP")
    ap.add_argument("--start", default="2025-08-01")
    ap.add_argument("--end", default="2026-01-31")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config.ensure_dirs(); TRUTH_DIR.mkdir(exist_ok=True)
    client = FirmsClient(dry_run=a.dry_run)
    df = client.fetch_range(a.source, date.fromisoformat(a.start), date.fromisoformat(a.end))
    if a.dry_run:
        total = sum(p["est_tx"] for p in client.planned)
        print(f"planned {len(client.planned)} calls, ~{total} tx (budget {config.TX_LIMIT_DEFAULT}/10 min)")
        return
    out = TRUTH_DIR / f"{a.source}_{a.start}_{a.end}.parquet"
    df.to_parquet(out, index=False)
    log.info("saved %d rows -> %s; sp_type counts %s; status %s", len(df), out,
             df.sp_type.value_counts().to_dict() if "sp_type" in df else "n/a", client.status())


if __name__ == "__main__":
    main()
