"""Universe selection and parsing, offline: requests.get returns canned Wikipedia-like HTML."""
from __future__ import annotations

import copy
import logging

import pytest

from scanner import cli, universe

SP500_ROWS = [("BRK.B", "Berkshire Hathaway", "Financials"),
              ("AAPL", "Apple Inc.", "Information Technology"),
              (" msft ", "Microsoft", "Information Technology"),
              ("BF.B", "Brown-Forman", "Consumer Staples")]


def _page(rows, cols=("Symbol", "Security", "GICS Sector")):
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>" for r in rows)
    decoy = "<table><tr><th>Date</th><th>Ticker added</th></tr><tr><td>2026</td><td>XYZ</td></tr></table>"
    return f"<html><body><table>{head and '<tr>' + head + '</tr>'}{body}</table>{decoy}</body></html>"


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


@pytest.fixture
def sp_cfg(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["universe"]["sp500"].update(saved_file=str(tmp_path / "sp500.csv"), min_count=3, max_count=5)
    return c


def test_sp500_is_the_default(cfg):
    u = universe.universe_config(cfg)
    assert u["name"] == "sp500" and u["benchmark"] == "SPY" and u["ticker_column"] == "Symbol"
    assert (u["min_count"], u["max_count"]) == (495, 510)
    assert "List_of_S%26P_500_companies" in u["url"]


def test_nasdaq100_block_kept(cfg):
    u = universe.universe_config(cfg, "nasdaq100")
    assert u["benchmark"] == "QQQ" and (u["min_count"], u["max_count"]) == (95, 105)


def test_unknown_universe_rejected(cfg):
    with pytest.raises(ValueError):
        universe.universe_config(cfg, "dax")


def test_parse_normalizes_and_keeps_sector(sp_cfg, monkeypatch):
    monkeypatch.setattr(universe.requests, "get", lambda *a, **k: _Resp(_page(SP500_ROWS)))
    df = universe.fetch_universe(sp_cfg)
    assert list(df["ticker"]) == ["BRK-B", "AAPL", "MSFT", "BF-B"]
    assert dict(zip(df["ticker"], df["sector"]))["BRK-B"] == "Financials"
    saved = universe.ticker_info(sp_cfg)
    assert saved["MSFT"] == ("Microsoft", "Information Technology")


def test_count_out_of_range_fails_loudly(sp_cfg, monkeypatch):
    monkeypatch.setattr(universe.requests, "get", lambda *a, **k: _Resp(_page(SP500_ROWS[:2])))
    with pytest.raises(RuntimeError, match="expected 3..5"):
        universe.fetch_universe(sp_cfg)


def test_fetch_failure_falls_back_to_saved_list(sp_cfg, monkeypatch, caplog):
    monkeypatch.setattr(universe.requests, "get", lambda *a, **k: _Resp(_page(SP500_ROWS)))
    universe.fetch_universe(sp_cfg)

    def boom(*a, **k):
        raise ConnectionError("blocked")
    monkeypatch.setattr(universe.requests, "get", boom)
    with caplog.at_level(logging.WARNING):
        df = universe.fetch_universe(sp_cfg)
    assert list(df["ticker"]) == ["BRK-B", "AAPL", "MSFT", "BF-B"]
    assert "using saved file" in caplog.text


def test_cli_universe_flag_overrides_config(monkeypatch):
    seen = {}

    def fake_run(cfg, **kwargs):
        seen["active"] = cfg["universe"]["active"]
        return 0
    monkeypatch.setattr(cli, "run", fake_run)
    assert cli.main(["--universe", "nasdaq100"]) == 0
    assert seen["active"] == "nasdaq100"
    with pytest.raises(ValueError):
        cli.main(["--universe", "dax"])
