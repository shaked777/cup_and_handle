"""Daily OHLCV from yfinance with a per-ticker parquet cache."""
from __future__ import annotations

import json
import logging
import re
import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import resolve_path

log = logging.getLogger(__name__)

FIELDS = ["Open", "High", "Low", "Close", "Volume"]


@dataclass(frozen=True)
class Window:
    start: date          # first calendar date requested
    end: date            # last calendar date requested (as-of or today)
    end_effective: date  # last date whose bar is final (end, or end - 1 while today's session runs)


def history_window(cfg: dict, as_of: date | None = None, now: datetime | None = None) -> Window:
    dcfg = cfg["data"]
    now = now or datetime.now(ZoneInfo(dcfg["session_timezone"]))
    today = now.date()
    end = min(as_of, today) if as_of else today
    start = (pd.Timestamp(end) - pd.DateOffset(years=dcfg["history_years"])).date()
    session_done = now.time() >= dtime.fromisoformat(dcfg["session_close_time"])
    end_effective = end if (end < today or session_done) else end - timedelta(days=1)
    return Window(start, end, end_effective)


# ---------------------------------------------------------------- cache

def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker)


def _cache_paths(cfg: dict, ticker: str):
    d = resolve_path(cfg["data"]["cache_dir"])
    return d / f"{_safe_name(ticker)}.parquet", d / f"{_safe_name(ticker)}.meta.json"


def _read_cache(cfg: dict, ticker: str) -> tuple[pd.DataFrame, dict] | None:
    pq, meta_path = _cache_paths(cfg, ticker)
    if not (pq.exists() and meta_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text())
        meta["start"] = date.fromisoformat(meta["start"])
        meta["end"] = date.fromisoformat(meta["end"])
        meta["downloaded_at"] = datetime.fromisoformat(meta["downloaded_at"])
        return pd.read_parquet(pq), meta
    except Exception as exc:
        log.warning("cache: unreadable entry for %s (%s), ignoring", ticker, exc)
        return None


def _write_cache(cfg: dict, ticker: str, df: pd.DataFrame, meta: dict) -> None:
    pq, meta_path = _cache_paths(cfg, ticker)
    pq.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq)
    meta_path.write_text(json.dumps({
        "start": meta["start"].isoformat(),
        "end": meta["end"].isoformat(),
        "downloaded_at": meta["downloaded_at"].isoformat(),
    }))


def _is_fresh(meta: dict, cfg: dict, now_utc: datetime) -> bool:
    age_h = (now_utc - meta["downloaded_at"]).total_seconds() / 3600
    return age_h < cfg["data"]["cache_max_age_hours"]


def _covers(meta: dict, window: Window) -> bool:
    return meta["start"] <= window.start and meta["end"] >= window.end_effective


# ---------------------------------------------------------------- download

def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "Date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[FIELDS].astype("float64")


def yf_download(tickers: list[str], start: date, end_inclusive: date, cfg: dict):
    """One yfinance call. Returns ({ticker: frame}, {ticker: error text})."""
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(
            tickers, start=start.isoformat(),
            end=(end_inclusive + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
            interval="1d", auto_adjust=True, group_by="ticker", progress=False,
            threads=True, timeout=cfg["data"]["request_timeout_sec"],
        )
    errors = {}
    try:
        errors = {str(k): str(v) for k, v in yf.shared._ERRORS.items()}
    except Exception:
        pass
    out = {}
    if raw is None or raw.empty:
        return out, errors
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        if multi:
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t]
        elif len(tickers) == 1:
            df = raw
        else:
            continue
        if not set(FIELDS).issubset(df.columns):
            continue
        df = df[FIELDS].dropna(how="all")
        if not df.empty:
            out[t] = _normalize_frame(df)
    return out, errors


def _download_batch(batch, start, end, cfg, downloader):
    """Download with retry and exponential backoff on rate limits or empty results."""
    dcfg = cfg["data"]
    got, pending, last_err = {}, list(batch), {}
    for attempt in range(dcfg["max_retries"] + 1):
        try:
            frames, errors = downloader(pending, start, end, cfg)
        except Exception as exc:  # rate limit or network error raised by yfinance
            frames, errors = {}, {t: f"{type(exc).__name__}: {exc}" for t in pending}
        got.update(frames)
        last_err.update(errors)
        pending = [t for t in pending if t not in frames]
        if not pending:
            break
        if attempt < dcfg["max_retries"]:
            wait = dcfg["backoff_base_sec"] * 2 ** attempt
            log.warning("data: %d ticker(s) empty or rate-limited (%s); retry %d/%d in %ss",
                        len(pending), ",".join(pending[:5]), attempt + 1, dcfg["max_retries"], wait)
            time.sleep(wait)
    failed = {t: f"download failed after {dcfg['max_retries']} retries"
                 + (f" ({last_err[t][:120]})" if t in last_err else " (empty result)")
              for t in pending}
    return got, failed


# ---------------------------------------------------------------- validation

def clean_prices(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame | None, str | None]:
    """Drop non-trading rows; reject frames with missing or inconsistent prices or too few bars."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"], how="all")
    if df[["Open", "High", "Low", "Close"]].isna().any().any():
        return None, "broken data: missing prices inside the series"
    if (df[["Open", "High", "Low", "Close"]] <= 0).any().any():
        return None, "broken data: non-positive prices"
    if (df["High"] < df["Low"]).any():
        return None, "broken data: high below low"
    df = df.assign(Volume=df["Volume"].fillna(0.0))
    if len(df) < cfg["data"]["min_bars"]:
        return None, f"only {len(df)} bars (< data.min_bars {cfg['data']['min_bars']})"
    return df, None


def slice_window(df: pd.DataFrame, window: Window) -> pd.DataFrame:
    """Bars from window.start to window.end_effective inclusive: nothing after the as-of date."""
    return df.loc[(df.index >= pd.Timestamp(window.start))
                  & (df.index <= pd.Timestamp(window.end_effective))]


def load_prices(tickers: list[str], cfg: dict, window: Window, refresh: bool = False,
                downloader=None, now_utc: datetime | None = None):
    """Return ({ticker: OHLCV frame within window}, {ticker: skip reason})."""
    downloader = downloader or yf_download  # looked up at call time, so tests can replace it
    dcfg = cfg["data"]
    now_utc = now_utc or datetime.now(timezone.utc)
    frames, skipped, to_fetch, stale = {}, {}, [], {}

    for t in tickers:
        cached = None if refresh else _read_cache(cfg, t)
        if cached and _is_fresh(cached[1], cfg, now_utc):
            if _covers(cached[1], window):
                frames[t] = cached[0]
                continue
            stale[t] = cached  # fresh but too short: merge new download into it
        to_fetch.append(t)

    if to_fetch:
        log.info("data: downloading %d ticker(s) %s..%s, %d cached",
                 len(to_fetch), window.start, window.end_effective, len(frames))
    bs = dcfg["batch_size"]
    for i in range(0, len(to_fetch), bs):
        if i:
            time.sleep(dcfg["pause_between_batches_sec"])
        batch = to_fetch[i:i + bs]
        got, failed = _download_batch(batch, window.start, window.end, cfg, downloader)
        skipped.update(failed)
        for t, df in got.items():
            df = df.loc[df.index <= pd.Timestamp(window.end_effective)]  # drop an unfinished session
            meta = {"start": window.start, "end": window.end_effective, "downloaded_at": now_utc}
            old = stale.get(t)
            if old is not None:
                odf, ometa = old
                overlaps = (window.start <= ometa["end"] + timedelta(days=1)
                            and window.end_effective >= ometa["start"] - timedelta(days=1))
                if overlaps:
                    df = pd.concat([odf[~odf.index.isin(df.index)], df]).sort_index()
                    meta = {"start": min(window.start, ometa["start"]),
                            "end": max(window.end_effective, ometa["end"]),
                            "downloaded_at": ometa["downloaded_at"]}
            _write_cache(cfg, t, df, meta)
            frames[t] = df

    out = {}
    for t in tickers:
        if t not in frames:
            continue
        df, reason = clean_prices(slice_window(frames[t], window), cfg)
        if reason:
            skipped[t] = reason
        else:
            out[t] = df
    return out, skipped


def to_arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "dates": df.index.values.astype("datetime64[D]"),
        "open": df["Open"].to_numpy(float), "high": df["High"].to_numpy(float),
        "low": df["Low"].to_numpy(float), "close": df["Close"].to_numpy(float),
        "volume": df["Volume"].to_numpy(float),
    }
