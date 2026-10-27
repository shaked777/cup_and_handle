"""--as-of D on full data must give exactly the result of data physically truncated at D."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pandas as pd
import pytest

from conftest import make_series
from scanner import data
from scanner.cli import analyze

TICKER = "SYN"
EXTRA = 40


def _no_network(*_args, **_kwargs):
    raise AssertionError("test must not download")


def _cfg_with_cache(cfg, path):
    c = copy.deepcopy(cfg)
    c["data"]["cache_dir"] = str(path)
    return c


def _seed_cache(cfg, df):
    meta = {"start": df.index[0].date() - pd.Timedelta(days=5 * 365),
            "end": df.index[-1].date(), "downloaded_at": datetime.now(timezone.utc)}
    data._write_cache(cfg, TICKER, df, meta)


def _load(cfg, as_of):
    window = data.history_window(cfg, as_of)
    frames, skipped = data.load_prices([TICKER], cfg, window, downloader=_no_network)
    assert not skipped, skipped
    return frames[TICKER]


def _summary(best, cands):
    def one(c):
        return (c.kind, c.stage, c.target_stage, c.a, c.b, c.c, c.h, c.e, round(c.score, 9),
                tuple((r.name, r.passed, None if r.value is None else round(r.value, 9)) for r in c.rules))
    return (one(best) if best else None, [one(c) for c in cands])


@pytest.fixture
def full_series():
    df, truth = make_series(tail="breakout", extra_bars=EXTRA)
    return df, len(df) - EXTRA - 1  # index of the breakout bar


def test_as_of_matches_truncated(cfg, tmp_path, full_series):
    df, e = full_series
    cfg_full = _cfg_with_cache(cfg, tmp_path / "full")
    _seed_cache(cfg_full, df)
    for i in list(range(e - 6, e + EXTRA, 3)) + [len(df) - 1]:
        as_of = df.index[i].date()
        cfg_cut = _cfg_with_cache(cfg, tmp_path / f"cut{i}")
        _seed_cache(cfg_cut, df.iloc[: i + 1])
        a = _load(cfg_full, as_of)
        b = _load(cfg_cut, as_of)
        pd.testing.assert_frame_equal(a, b)
        assert a.index[-1].date() == as_of
        assert _summary(*analyze(a, cfg)[:2]) == _summary(*analyze(b, cfg)[:2]), f"as-of {as_of}"


def test_future_breakout_not_seen(cfg, tmp_path, full_series):
    df, e = full_series
    cfg_full = _cfg_with_cache(cfg, tmp_path / "full")
    _seed_cache(cfg_full, df)
    before, _, _ = analyze(_load(cfg_full, df.index[e - 1].date()), cfg)
    on, _, _ = analyze(_load(cfg_full, df.index[e].date()), cfg)
    assert before.stage == "NEAR_PIVOT" and before.e is None
    assert on.stage == "BREAKOUT" and on.e == e


def test_unfinished_session_dropped(cfg):
    """A bar dated today in New York is dropped before the session close time."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg["data"]["session_timezone"])
    during = datetime(2026, 10, 27, 12, 0, tzinfo=tz)
    after = datetime(2026, 10, 27, 17, 0, tzinfo=tz)
    assert data.history_window(cfg, now=during).end_effective.isoformat() == "2026-10-26"
    assert data.history_window(cfg, now=after).end_effective.isoformat() == "2026-10-27"
