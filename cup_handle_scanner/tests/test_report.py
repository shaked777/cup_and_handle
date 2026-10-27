"""End-to-end scan of synthetic tickers through cli.run, offline and entirely inside tmp_path."""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone

import pandas as pd

from scanner import cli, data
from scanner.synthetic import make_series

SERIES = {  # ticker: (make_series kwargs, sector)
    "SYNB": (dict(shape="halfsine", tail="breakout"), "Industrials"),
    "SYNN": (dict(shape="halfsine", tail="near"), "Health Care"),
    "SYNO": (dict(shape="halfsine", tail="near", rim_ratio=0.95), "Health Care"),
}
BENCH_EXTRA = 1  # the benchmark runs one bar past the others, so it sets the last session


def _no_network(*_args, **_kwargs):
    raise AssertionError("test must not download")


def test_offline_scan_writes_only_to_tmp(cfg, tmp_path, monkeypatch):
    c = copy.deepcopy(cfg)
    c["data"]["cache_dir"] = str(tmp_path / "cache")
    c["report"]["output_dir"] = str(tmp_path / "output")
    c["report"]["max_charts"] = 2
    ucfg = c["universe"][c["universe"]["active"]]
    ucfg["saved_file"] = str(tmp_path / "universe.csv")
    with open(ucfg["saved_file"], "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "company", "sector"])
        for t, (_, sector) in SERIES.items():
            w.writerow([t, f"Synthetic {t}", sector])

    frames = {t: make_series(seed=1, **kw)[0] for t, (kw, _) in SERIES.items()}
    frames[ucfg["benchmark"]] = make_series("halfsine", tail="breakout", extra_bars=BENCH_EXTRA, seed=2)[0]
    as_of = frames[ucfg["benchmark"]].index[-1].date()
    for t, df in frames.items():  # cached window runs to as_of even where a series ends earlier
        data._write_cache(c, t, df, {"start": df.index[0].date() - pd.Timedelta(days=5 * 365),
                                     "end": as_of, "downloaded_at": datetime.now(timezone.utc)})
    monkeypatch.setattr(data, "yf_download", _no_network)

    assert cli.run(c, tickers=list(SERIES), as_of=as_of) == 0

    out = tmp_path / "output" / cli.CUSTOM_RUN / as_of.isoformat()
    assert sorted(p.name for p in out.iterdir()) == ["charts", "report.html", "report.md",
                                                      "results.csv", "results.json"]
    assert len(list((out / "charts").glob("*.png"))) == 2           # report.max_charts
    html = (out / "report.html").read_text()
    assert html.count("data:image/png;base64,") == 2
    assert "No chart (beyond the top 2)" in html
    assert "Results by sector and stage" in html and "Health Care" in html

    rows = {r["ticker"]: r for r in csv.DictReader(open(out / "results.csv"))}
    assert set(rows) == set(SERIES)
    assert rows["SYNB"]["stage"] == "BREAKOUT" and rows["SYNN"]["stage"] == "NEAR_PIVOT"
    assert rows["SYNO"]["sector"] == "Health Care"
    assert float(rows["SYNO"]["overhead_pct"]) >= c["flags"]["left_rim_overhead_min_pct"]
    assert "left_rim_overhead +" in rows["SYNO"]["flags"]
    assert "left_rim_overhead" not in rows["SYNN"]["flags"]
