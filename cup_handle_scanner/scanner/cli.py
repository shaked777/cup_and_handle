"""Command-line entry point.

python -m scanner.cli [--config PATH] [--tickers A,B] [--refresh] [--as-of YYYY-MM-DD]
                      [--explain TICKER]

main() is the single non-interactive entry point; a scheduler can call it directly.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import data, universe
from .config import load_config, resolve_path
from .detector import STAGES, Bars, Candidate, best_near_miss, detect, explain_order, select_best
from .scoring import relative_strength, score_all, sma

log = logging.getLogger("scanner")


@dataclass
class TickerResult:
    ticker: str
    company: str
    df: pd.DataFrame
    bars: Bars
    best: Candidate | None
    near_miss: Candidate | None
    n_candidates: int
    chart: str | None = None


def analyze(df: pd.DataFrame, cfg: dict, rs: float | None = None):
    """Detect, score and pick the best candidate for one ticker's bars (all up to T)."""
    bars = Bars.from_frame(df)
    cands = detect(bars, cfg)
    score_all(cands, bars, cfg, cfg["scoring"]["no_data_value"] if rs is None else rs)
    return select_best(cands), cands, bars


def market_context(df: pd.DataFrame | None, cfg: dict) -> dict | None:
    if df is None or df.empty:
        return None
    sc = cfg["scoring"]
    close = df["Close"].to_numpy(float)
    fast, slow = sma(close, sc["sma_fast_days"]), sma(close, sc["sma_slow_days"])
    return {"ticker": cfg["universe"]["benchmark"], "date": str(df.index[-1].date()),
            "close": float(close[-1]), "sma_fast": fast, "sma_slow": slow,
            "fast_days": sc["sma_fast_days"], "slow_days": sc["sma_slow_days"],
            "above_fast": fast is not None and close[-1] > fast,
            "above_slow": slow is not None and close[-1] > slow}


# ---------------------------------------------------------------- explain

def _fmt_cand_header(i: int, c: Candidate, bars: Bars) -> str:
    stage = c.stage or f"REJECTED as {c.target_stage} ({len(c.failed)} failed)"
    parts = [f"a {bars.date(c.a)}", f"b {bars.date(c.b)}"]
    if c.c is not None:
        parts.append(f"c {bars.date(c.c)}" + ("" if c.c_confirmed else " (unconfirmed)"))
    if c.h is not None:
        parts.append(f"h {bars.date(c.h)}")
    if c.e is not None:
        parts.append(f"e {bars.date(c.e)}")
    flags = f"  [{', '.join(c.flags)}]" if c.flags else ""
    return f"#{i:<2} {stage}  score {c.score:.1f}  " + "  ".join(parts) + flags


def print_explain(ticker: str, cands: list[Candidate], best: Candidate | None, bars: Bars, cfg: dict) -> None:
    top = cfg["report"]["explain_top"]
    print(f"\n{ticker}: last bar {bars.date(bars.T)} ({bars.T + 1} bars), config {cfg['_config_name']}")
    if best:
        lv = best.levels
        print(f"RESULT: {best.stage}  score {best.score:.1f}  pivot {lv['pivot']:.2f}  "
              f"close {bars.close[-1]:.2f} ({lv['distance_to_pivot_pct']:+.1f}% to pivot)  "
              f"stop {lv['stop']:.2f}  target {lv['target']:.2f}")
    else:
        print("RESULT: REJECTED (no candidate passes every rule)")
    print(f"{len(cands)} candidates evaluated; top {min(top, len(cands))} below. "
          "Status in caps = required for that candidate's stage; lower case = informational.\n")
    for i, c in enumerate(explain_order(cands)[:top], 1):
        print(_fmt_cand_header(i, c, bars))
        for r in c.rules:
            st = r.status() if r.required else r.status().lower()
            print(f"     {st:<5} {r.name:<25} {r.shown:<48} {r.threshold}")
        print()


# ---------------------------------------------------------------- scan

def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="python -m scanner.cli",
                                description="Scan Nasdaq-100 daily bars for cup-and-handle patterns.")
    p.add_argument("--config", help="YAML file deep-merged over config.yaml")
    p.add_argument("--tickers", help="comma-separated symbols to scan instead of the index")
    p.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    p.add_argument("--as-of", dest="as_of", type=date.fromisoformat,
                   help="end the data window at this date (YYYY-MM-DD)")
    p.add_argument("--explain", metavar="TICKER",
                   help="scan only this ticker and print its top candidates with every rule")
    return p.parse_args(argv)


def run(cfg: dict, tickers: list[str] | None = None, as_of: date | None = None,
        refresh: bool = False, explain: str | None = None) -> int:
    window = data.history_window(cfg, as_of)
    names: dict[str, str] = {}
    if explain:
        tickers = [explain.strip().upper()]
    elif tickers:
        tickers = [t.strip().upper() for t in tickers if t.strip()]
    else:
        uni = universe.fetch_universe(cfg)
        tickers = list(uni["ticker"])
    names = universe.company_names(cfg)

    bench = cfg["universe"]["benchmark"]
    fetch = list(tickers) + ([] if explain or bench in tickers else [bench])
    prices, skipped = data.load_prices(fetch, cfg, window, refresh=refresh)
    bench_df = prices.get(bench)
    if bench not in tickers:
        prices.pop(bench, None)
        skipped.pop(bench, None)
    scanned = {t: prices[t] for t in tickers if t in prices}
    if not scanned:
        log.error("no tickers with usable data; skipped: %s", skipped)
        return 1

    t0 = time.perf_counter()
    rs = relative_strength({t: df["Close"].to_numpy(float) for t, df in scanned.items()}, cfg)
    results: list[TickerResult] = []
    explained = None
    for t, df in scanned.items():
        best, cands, bars = analyze(df, cfg, rs[t])
        if explain:
            explained = (cands, best, bars)
        results.append(TickerResult(t, names.get(t, ""), df, bars, best,
                                    None if best else best_near_miss(cands), len(cands)))
    elapsed = time.perf_counter() - t0
    log.info("detection: %d tickers, %d candidates in %.1fs",
             len(results), sum(r.n_candidates for r in results), elapsed)

    if explain:
        cands, best, bars = explained
        print_explain(tickers[0], cands, best, bars, cfg)
        return 0

    from . import charts, report  # imported here so --explain stays light

    found = sorted([r for r in results if r.best], key=lambda r: (STAGES.index(r.best.stage), -r.best.score))
    near = sorted([r for r in results if r.near_miss],
                  key=lambda r: -r.near_miss.score)[: cfg["report"]["near_misses"]]
    last_session = (bench_df.index[-1] if bench_df is not None
                    else max(df.index[-1] for df in scanned.values())).date()

    out_dir = resolve_path(cfg["report"]["output_dir"]) / last_session.isoformat()
    chart_dir = out_dir / "charts"
    if chart_dir.exists():
        shutil.rmtree(chart_dir)
    chart_dir.mkdir(parents=True)
    for r in found:
        path = chart_dir / f"{data._safe_name(r.ticker)}.png"
        charts.render_chart(r.ticker, r.df, r.bars, r.best, cfg, path, r.company)
        r.chart = f"charts/{path.name}"

    ctx = {
        "run_time": datetime.now(ZoneInfo(cfg["report"]["run_timezone"])),
        "last_session": last_session,
        "as_of": as_of,
        "window": window,
        "scanned": len(scanned),
        "requested": len(tickers),
        "skipped": skipped,
        "market": market_context(bench_df, cfg),
        "config_name": cfg["_config_name"],
        "detection_sec": elapsed,
    }
    report.write_all(found, near, ctx, cfg, out_dir)
    report.print_console(found, ctx, cfg, out_dir)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    cfg = load_config(args.config)
    tickers = args.tickers.split(",") if args.tickers else None
    return run(cfg, tickers=tickers, as_of=args.as_of, refresh=args.refresh, explain=args.explain)


if __name__ == "__main__":
    sys.exit(main())
