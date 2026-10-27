"""Index constituents (S&P 500 or Nasdaq-100) from Wikipedia, with a saved-file fallback."""
from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests

from .config import resolve_path

log = logging.getLogger(__name__)

COLUMNS = ["ticker", "company", "sector"]


def normalize_ticker(t: str) -> str:
    """Yahoo style: strip, upper-case, "." -> "-" (BRK.B -> BRK-B)."""
    return str(t).strip().upper().replace(".", "-")


def active_name(cfg: dict) -> str:
    return cfg["universe"]["active"]


def universe_config(cfg: dict, name: str | None = None) -> dict:
    """The block for one universe, plus the shared request settings."""
    u = cfg["universe"]
    name = name or u["active"]
    if name not in u or not isinstance(u[name], dict):
        known = [k for k, v in u.items() if isinstance(v, dict)]
        raise ValueError(f"unknown universe {name!r}; configured: {', '.join(known)}")
    return {"name": name, "user_agent": u["user_agent"], "request_timeout_sec": u["request_timeout_sec"], **u[name]}


def _clean_col(col) -> str:
    # "Ticker[3]" -> "Ticker"
    return re.sub(r"\[.*?\]", "", str(col)).strip()


def _find_table(html: str, ucfg: dict) -> pd.DataFrame | None:
    """The table with the ticker column whose row count is closest to the expected range."""
    target = (ucfg["min_count"] + ucfg["max_count"]) / 2
    best = None
    for table in pd.read_html(io.StringIO(html)):
        cols = {_clean_col(c): c for c in table.columns}
        tcol = cols.get(ucfg["ticker_column"])
        if tcol is None:
            continue
        ncol, scol = cols.get(ucfg["company_column"]), cols.get(ucfg["sector_column"])
        df = pd.DataFrame({
            "ticker": table[tcol].astype(str),
            "company": table[ncol].astype(str) if ncol is not None else "",
            "sector": table[scol].astype(str) if scol is not None else "",
        })
        if best is None or abs(len(df) - target) < abs(len(best) - target):
            best = df
    return best


def fetch_universe(cfg: dict, name: str | None = None) -> pd.DataFrame:
    """Download the constituents table; fall back to the saved CSV if every URL fails."""
    ucfg = universe_config(cfg, name)
    saved = resolve_path(ucfg["saved_file"])
    table = None
    for url in [ucfg["url"], *ucfg.get("fallback_urls", [])]:
        try:
            resp = requests.get(url, headers={"User-Agent": ucfg["user_agent"]},
                                timeout=ucfg["request_timeout_sec"])
            resp.raise_for_status()
            table = _find_table(resp.text, ucfg)
            if table is not None:
                log.info("universe %s: %d rows from %s", ucfg["name"], len(table), url)
                break
            log.warning("universe %s: no %r table at %s", ucfg["name"], ucfg["ticker_column"], url)
        except Exception as exc:  # network, HTTP or parse error
            log.warning("universe %s: fetch failed for %s: %s", ucfg["name"], url, exc)

    if table is None:
        if not saved.exists():
            raise RuntimeError(f"universe {ucfg['name']}: fetch failed and no saved file at {saved}")
        log.warning("universe %s: using saved file %s (fetch failed)", ucfg["name"], saved)
        return load_saved_universe(cfg, ucfg["name"])

    table["ticker"] = table["ticker"].map(normalize_ticker)
    table["company"] = table["company"].str.strip()
    table["sector"] = table["sector"].str.strip()
    table = table[table["ticker"].str.len() > 0].drop_duplicates("ticker").reset_index(drop=True)
    n = len(table)
    if not ucfg["min_count"] <= n <= ucfg["max_count"]:
        raise RuntimeError(f"universe {ucfg['name']}: {n} tickers parsed, "
                           f"expected {ucfg['min_count']}..{ucfg['max_count']}")
    saved.parent.mkdir(parents=True, exist_ok=True)
    table[COLUMNS].to_csv(saved, index=False)
    return table[COLUMNS]


def load_saved_universe(cfg: dict, name: str | None = None) -> pd.DataFrame:
    saved = resolve_path(universe_config(cfg, name)["saved_file"])
    if not saved.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(saved, dtype=str, keep_default_na=False)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[COLUMNS]


def ticker_info(cfg: dict, name: str | None = None) -> dict[str, tuple[str, str]]:
    """{ticker: (company, sector)} from the saved list of a universe."""
    df = load_saved_universe(cfg, name)
    return {t: (c, s) for t, c, s in zip(df["ticker"], df["company"], df["sector"])}
