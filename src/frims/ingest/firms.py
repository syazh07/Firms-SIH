"""NASA FIRMS area-API client with transaction-budget guard, on-disk cache and backoff.

Facts (from firms_api_use.ipynb): a MAP_KEY has 5000 transactions per 10-minute window and the cost of a call
scales with the volume returned (~1 tx per ~700 rows), so we estimate cost from previously observed calls,
check `mapkey_status` before/while batching, and never re-fetch a window we already have on disk.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from frims import config

log = logging.getLogger(__name__)


class FirmsError(RuntimeError):
    pass


@dataclass
class TxStatus:
    used: int
    limit: int
    interval: str

    @property
    def remaining(self) -> int:
        return self.limit - self.used


@dataclass
class FirmsClient:
    map_key: str = config.MAP_KEY
    bbox: tuple[float, float, float, float] = config.BBOX
    raw_dir: Path = config.RAW_DIR / "firms"
    session: requests.Session = field(default_factory=requests.Session)
    dry_run: bool = False
    # observed cost per source, used to estimate future calls
    _cost_estimates: dict[str, int] = field(default_factory=dict)
    _calls_since_status: int = 0
    _last_status: TxStatus | None = None
    planned: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------------- status
    def status(self) -> TxStatus:
        if not self.map_key:
            raise FirmsError("MAP_KEY missing; put MAP_KEY=... in .env")
        url = config.FIRMS_STATUS_URL.format(key=self.map_key)
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        try:
            j = r.json()
        except ValueError as e:  # HTML error page
            raise FirmsError(f"mapkey_status returned non-JSON: {r.text[:200]}") from e
        st = TxStatus(int(j["current_transactions"]), int(j.get("transaction_limit", config.TX_LIMIT_DEFAULT)),
                      str(j.get("transaction_interval", "10 minutes")))
        self._last_status = st
        self._calls_since_status = 0
        return st

    def _estimate(self, source: str) -> int:
        return self._cost_estimates.get(source, config.TX_DEFAULT_ESTIMATE)

    def budget_guard(self, estimated_cost: int) -> None:
        """Block until the budget can absorb `estimated_cost` transactions."""
        if self._last_status is None or self._calls_since_status >= config.TX_STATUS_EVERY_N_CALLS:
            self.status()
        assert self._last_status is not None
        while self._last_status.used + estimated_cost > self._last_status.limit - config.TX_SAFETY_MARGIN:
            wait = 60
            log.warning("FIRMS budget nearly exhausted (%s/%s used); sleeping %ss for window reset",
                        self._last_status.used, self._last_status.limit, wait)
            time.sleep(wait)
            self.status()

    # ----------------------------------------------------------------- cache
    def _cache_path(self, source: str, start: date, days: int) -> Path:
        bbox_tag = "_".join(f"{v:g}" for v in self.bbox)
        return self.raw_dir / source / f"{start.isoformat()}_{days}d_{bbox_tag}.parquet"

    @staticmethod
    def _is_fresh(path: Path, max_age_min: int) -> bool:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return age < timedelta(minutes=max_age_min)

    # ----------------------------------------------------------------- fetch
    def fetch_window(self, source: str, start: date, days: int) -> pd.DataFrame:
        """Fetch one <=10-day window for a source, using the cache when the window is complete."""
        if days < 1 or days > config.FIRMS_MAX_DAYS_PER_CALL:
            raise ValueError(f"days must be 1..{config.FIRMS_MAX_DAYS_PER_CALL} for the FIRMS area endpoint")
        today = datetime.now(timezone.utc).date()
        end = start + timedelta(days=days - 1)
        path = self._cache_path(source, start, days)
        window_complete = end < today
        if path.exists() and (window_complete or self._is_fresh(path, config.FIRMS_REFRESH_MINUTES)):
            log.info("cache hit %s %s/%dd", source, start, days)
            return pd.read_parquet(path)

        estimate = self._estimate(source)
        if self.dry_run:
            self.planned.append({"source": source, "start": start.isoformat(), "days": days, "est_tx": estimate})
            return pd.DataFrame()

        self.budget_guard(estimate)
        bbox = ",".join(f"{v:g}" for v in self.bbox)
        url = config.FIRMS_AREA_URL.format(key=self.map_key, source=source, bbox=bbox, days=days)
        if end < today:
            url += f"/{start.isoformat()}"
        elif start < today:
            # partial window ending today: FIRMS interprets `days` as "last N days" when no DATE given
            url += f"/{start.isoformat()}"
        before = self._last_status.used if self._last_status else None
        df = self._get_csv(url)
        self._calls_since_status += 1
        # learn the real cost for this source
        try:
            after = self.status().used
            if before is not None and after >= before:
                self._cost_estimates[source] = max(1, after - before)
        except FirmsError:
            pass
        df = normalise(df, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        log.info("fetched %s %s/%dd -> %d rows (est cost %s tx)", source, start, days, len(df),
                 self._cost_estimates.get(source, "?"))
        time.sleep(config.FIRMS_SLEEP_BETWEEN_CALLS_S)
        return df

    def _get_csv(self, url: str, retries: int = 4) -> pd.DataFrame:
        delay = 5.0
        for attempt in range(retries):
            try:
                r = self.session.get(url, timeout=(30, 600))  # SP windows in fire season are tens of MB
            except requests.RequestException as e:
                log.warning("FIRMS network error (attempt %d): %s", attempt + 1, str(e)[:120])
                time.sleep(delay); delay *= 2
                continue
            body = r.text
            if r.status_code == 200 and not body.lstrip().startswith("<"):
                if body.strip() == "" or body.lower().startswith("invalid"):
                    raise FirmsError(f"FIRMS error body: {body[:200]}")
                return pd.read_csv(io.StringIO(body))
            if 400 <= r.status_code < 500 and r.status_code != 429:
                raise FirmsError(f"FIRMS rejected request ({r.status_code}): {body[:200].strip()}")
            log.warning("FIRMS call failed (%s, attempt %d): %s", r.status_code, attempt + 1, body[:120].strip())
            time.sleep(delay)
            delay *= 2
        raise FirmsError(f"FIRMS call failed after {retries} attempts: {url.replace(self.map_key, '***')}")

    def _cached_windows(self, source: str) -> list[tuple[date, date, Path]]:
        """(start, end_inclusive, path) for every completed cached window of this source and bbox."""
        d = self.raw_dir / source
        if not d.exists():
            return []
        bbox_tag = "_".join(f"{v:g}" for v in self.bbox)
        out = []
        for p in d.glob(f"*_{bbox_tag}.parquet"):
            try:
                start_s, days_s = p.name.split("_")[:2]
                st = date.fromisoformat(start_s); days = int(days_s.rstrip("d"))
            except ValueError:
                continue
            out.append((st, st + timedelta(days=days - 1), p))
        return sorted(out)

    def fetch_range(self, source: str, start: date, end: date) -> pd.DataFrame:
        """Fetch [start, end] inclusive. Any part already covered by cached windows (whatever their alignment) is
        read from disk; only uncovered gaps are requested from FIRMS in <=5-day windows."""
        today = datetime.now(timezone.utc).date()
        frames, covered = [], []
        for st, en, path in self._cached_windows(source):
            if en < start or st > end:
                continue
            if en >= today and not self._is_fresh(path, config.FIRMS_REFRESH_MINUTES):
                continue  # stale partial window ending today: refetch
            frames.append(pd.read_parquet(path)); covered.append((max(st, start), min(en, end)))
        # gaps
        gaps, cur = [], start
        for st, en in sorted(covered):
            if st > cur:
                gaps.append((cur, st - timedelta(days=1)))
            cur = max(cur, en + timedelta(days=1))
        if cur <= end:
            gaps.append((cur, end))
        for gs, ge in gaps:
            c = gs
            while c <= ge:
                days = min(config.FIRMS_MAX_DAYS_PER_CALL, (ge - c).days + 1)
                try:
                    frames.append(self.fetch_window(source, c, days))
                except FirmsError as e:
                    log.warning("window %s %s/%dd skipped: %s", source, c, days, e)
                c += timedelta(days=days)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()
        df = _coerce_times(pd.concat(frames, ignore_index=True))
        df = df[(df.acq_date >= pd.Timestamp(start)) & (df.acq_date <= pd.Timestamp(end))]
        return df.drop_duplicates(subset=["source", "latitude", "longitude", "acq_date", "acq_time"])

    def fetch_all(self, days_back: int, sources: list[str] | None = None) -> pd.DataFrame:
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days_back - 1)
        frames = []
        for src in sources or config.SOURCES:
            try:
                frames.append(self.fetch_range(src, start, today))
            except FirmsError as e:
                log.error("skipping %s: %s", src, e)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame()
        df = _coerce_times(pd.concat(frames, ignore_index=True))
        return df.drop_duplicates(subset=["source", "latitude", "longitude", "acq_date", "acq_time"])

    def load_cached(self, days_back: int, sources: list[str] | None = None) -> pd.DataFrame:
        """Offline: read every cached window overlapping the range (no network)."""
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days_back - 1)
        frames = []
        for src in sources or config.SOURCES:
            for p in sorted((self.raw_dir / src).glob("*.parquet")) if (self.raw_dir / src).exists() else []:
                frames.append(pd.read_parquet(p))
        if not frames:
            return pd.DataFrame()
        df = _coerce_times(pd.concat(frames, ignore_index=True))
        df = df[df["acq_date"] >= pd.Timestamp(start)]
        return df.drop_duplicates(subset=["source", "latitude", "longitude", "acq_date", "acq_time"])


# ------------------------------------------------------------------ helpers
_CONF_LETTER = {"l": 0.3, "n": 0.6, "h": 0.9}


def _coerce_times(df: pd.DataFrame) -> pd.DataFrame:
    """Cached windows may carry different dtypes (datetime ns vs us, numeric vs string); concat then yields object."""
    for c in ("acq_date", "acq_datetime"):
        if c in df:
            df[c] = pd.to_datetime(df[c]).astype("datetime64[ns]")
    for c in ("latitude", "longitude", "bt", "bt2", "frp", "scan", "track", "conf_norm"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def normalise(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Harmonise VIIRS/MODIS column differences into one schema."""
    if df.empty:
        return df
    out = df.copy()
    out["source"] = source
    for c in ("latitude", "longitude"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    bad = out.latitude.isna() | out.longitude.isna()
    if bad.any():  # FIRMS occasionally appends a non-CSV line (e.g. an error message) to a response
        log.warning("%s: dropping %d rows with unparseable coordinates", source, int(bad.sum()))
        out = out[~bad]
    if "bright_ti4" in out:          # VIIRS
        out = out.rename(columns={"bright_ti4": "bt", "bright_ti5": "bt2"})
    elif "brightness" in out:        # MODIS
        out = out.rename(columns={"brightness": "bt", "bright_t31": "bt2"})
    conf = out["confidence"]
    if pd.api.types.is_numeric_dtype(conf):
        out["conf_norm"] = pd.to_numeric(conf, errors="coerce").fillna(50) / 100.0
    else:
        out["conf_norm"] = conf.astype(str).str.lower().str.strip().map(_CONF_LETTER).fillna(0.5)
    out["acq_date"] = pd.to_datetime(out["acq_date"])
    out["acq_time"] = pd.to_numeric(out["acq_time"], errors="coerce").fillna(0).astype(int)
    out["acq_datetime"] = out["acq_date"] + pd.to_timedelta(out["acq_time"] // 100, unit="h") + \
        pd.to_timedelta(out["acq_time"] % 100, unit="m")
    out["is_night"] = out["daynight"].astype(str).str.upper().eq("N")
    out["frp"] = pd.to_numeric(out["frp"], errors="coerce")
    if "type" in out:  # standard-processing products only: 0 veg fire, 1 volcano, 2 static land source, 3 offshore
        out["sp_type"] = pd.to_numeric(out["type"], errors="coerce").astype("Int64")
    keep = ["source", "satellite", "instrument", "latitude", "longitude", "bt", "bt2", "frp", "scan", "track",
            "acq_date", "acq_time", "acq_datetime", "daynight", "is_night", "confidence", "conf_norm", "version", "sp_type"]
    return out[[c for c in keep if c in out.columns]]


def data_availability(map_key: str = config.MAP_KEY) -> pd.DataFrame:
    return pd.read_csv(config.FIRMS_AVAIL_URL.format(key=map_key))
