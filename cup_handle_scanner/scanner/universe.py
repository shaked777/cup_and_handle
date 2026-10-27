"""Nasdaq-100 constituents from Wikipedia, with a saved-file fallback."""
from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests

from .config import resolve_path

log = logging.getLogger(__name__)

TICKER_COLUMNS = ("Ticker", "Symbol")
NAME_COLUMNS = ("Company", "Security", "Name")


def normalize_ticker(t: str) -> str:
    return str(t).strip().upper().replace(".", "-")


def _clean_col(col) -> str:
    # "Ticker[3]" -> "Ticker"
    return re.sub(r"\[.*?\]", "", str(col)).strip()


def _find_table(html: str, ucfg: dict) -> pd.DataFrame | None:
    """Pick the table with a Ticker/Symbol column whose row count is closest to the expected range."""
    target = (ucfg["min_count"] + ucfg["max_count"]) / 2
    best = None
    for table in pd.read_html(io.StringIO(html)):
        cols = {_clean_col(c): c for c in table.columns}
        tcol = next((cols[c] for c in TICKER_COLUMNS if c in cols), None)
        if tcol is None:
            continue
        ncol = next((cols[c] for c in NAME_COLUMNS if c in cols), None)
        df = pd.DataFrame({
            "ticker": table[tcol].astype(str),
            "company": table[ncol].astype(str) if ncol is not None else "",
        })
        if best is None or abs(len(df) - target) < abs(len(best) - target):
            best = df
    return best


def fetch_universe(cfg: dict) -> pd.DataFrame:
    """Download the constituents table; fall back to the saved CSV if every URL fails."""
    ucfg = cfg["universe"]
    saved = resolve_path(ucfg["saved_file"])
    urls = [ucfg["url"], *ucfg.get("fallback_urls", [])]
    table = None
    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": ucfg["user_agent"]},
                                timeout=ucfg["request_timeout_sec"])
            resp.raise_for_status()
            table = _find_table(resp.text, ucfg)
            if table is not None:
                log.info("universe: %d rows from %s", len(table), url)
                break
            log.warning("universe: no Ticker/Symbol table at %s", url)
        except Exception as exc:  # network, HTTP or parse error
            log.warning("universe: fetch failed for %s: %s", url, exc)

    if table is None:
        if not saved.exists():
            raise RuntimeError(f"universe: fetch failed and no saved file at {saved}")
        log.warning("universe: using saved file %s (fetch failed)", saved)
        return load_saved_universe(cfg)

    table["ticker"] = table["ticker"].map(normalize_ticker)
    table["company"] = table["company"].str.strip()
    table = table[table["ticker"].str.len() > 0].drop_duplicates("ticker").reset_index(drop=True)
    n = len(table)
    if not ucfg["min_count"] <= n <= ucfg["max_count"]:
        raise RuntimeError(
            f"universe: {n} tickers parsed, expected {ucfg['min_count']}..{ucfg['max_count']}")
    saved.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(saved, index=False)
    return table


def load_saved_universe(cfg: dict) -> pd.DataFrame:
    saved = resolve_path(cfg["universe"]["saved_file"])
    if not saved.exists():
        return pd.DataFrame(columns=["ticker", "company"])
    return pd.read_csv(saved, dtype=str, keep_default_na=False)


def company_names(cfg: dict) -> dict[str, str]:
    df = load_saved_universe(cfg)
    return dict(zip(df["ticker"], df["company"]))
