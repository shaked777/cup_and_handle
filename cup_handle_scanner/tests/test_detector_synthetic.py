"""Detector on synthetic series (scanner/synthetic.py); every test runs over 20 fixed seeds.

Layout: 120-bar prior uptrend (+40%), 150-bar cup 25% deep, 15-bar handle drifting 6% lower,
0.3% Gaussian noise per bar.

The scenario tests use the half-sine cup. Its rims are steep, so a, c and h each have a clear bar
and are checked within 5 bars. A round bottom is flat over many bars and has no single correct
bar, so b is checked by price: low[b] within 1% of the cup's true minimum price. The full-cosine
cup is flat at the rims too, so its left rim is also checked by price.
"""
from __future__ import annotations

import pytest

from conftest import SEEDS
from scanner.cli import analyze
from scanner.synthetic import make_series

TOL_BARS = 5
TOL_PRICE = 0.01
ROUND = "halfsine"


def _assert_bottom(best, bars, truth):
    low_b = bars.low[best.b]
    assert abs(low_b / truth["bottom_price"] - 1) <= TOL_PRICE, (low_b, truth["bottom_price"])


def _assert_bars(best, truth, keys="ach"):
    errors = {k: getattr(best, k) - truth[k] for k in keys if k in truth}
    assert all(abs(v) <= TOL_BARS for v in errors.values()), errors


def _true_candidate(cands, truth):
    """The best-scoring candidate whose a and c sit on the true rims."""
    hits = [c for c in cands if c.kind == "cup"
            and abs(c.a - truth["a"]) <= TOL_BARS and abs(c.c - truth["c"]) <= TOL_BARS]
    assert hits, "no candidate on the true rims"
    return max(hits, key=lambda c: c.score)


@pytest.mark.parametrize("seed", SEEDS)
def test_near_pivot(cfg, seed):
    df, truth = make_series(ROUND, tail="near", seed=seed)
    best, _, bars = analyze(df, cfg)
    assert best is not None and best.stage == "NEAR_PIVOT"
    _assert_bars(best, truth)
    _assert_bottom(best, bars, truth)
    assert best.levels["distance_to_pivot_pct"] == pytest.approx(-2.0, abs=0.3)
    assert best.levels["stop"] == pytest.approx(bars.low[best.h])


@pytest.mark.parametrize("seed", SEEDS)
def test_breakout_with_volume(cfg, seed):
    df, truth = make_series(ROUND, tail="breakout", seed=seed)
    best, _, bars = analyze(df, cfg)
    assert best is not None and best.stage == "BREAKOUT"
    assert best.e == len(df) - 1
    assert "volume_confirmed" in best.flags and "extended" not in best.flags
    assert best.m["volume_ratio"] >= cfg["breakout"]["volume_ratio_min"]
    _assert_bars(best, truth)
    _assert_bottom(best, bars, truth)


@pytest.mark.parametrize("seed", SEEDS)
def test_deep_handle_rejected(cfg, seed):
    df, truth = make_series(ROUND, handle_drop=0.20, tail="none", seed=seed)
    best, cands, _ = analyze(df, cfg)
    assert best is None
    assert "handle_depth_max" in {r.name for r in _true_candidate(cands, truth).failed}


@pytest.mark.parametrize("seed", SEEDS)
def test_cup_forming(cfg, seed):
    df, truth = make_series(ROUND, recovery=0.55, seed=seed)
    best, _, bars = analyze(df, cfg)
    assert best is not None and best.stage == "CUP_FORMING"
    assert best.kind == "forming" and best.c is None
    _assert_bars(best, truth, keys="a")
    _assert_bottom(best, bars, truth)
    assert best.m["recovery"] >= cfg["cup"]["forming_min_recovery"]
    assert best.levels["pivot"] == pytest.approx(bars.high[best.a])


@pytest.mark.parametrize("seed", SEEDS)
def test_straight_v_rejected_by_round_rule(cfg, seed):
    df, truth = make_series("v", tail="near", seed=seed)
    best, cands, _ = analyze(df, cfg)
    assert best is None
    cand = _true_candidate(cands, truth)
    assert [r.name for r in cand.failed] == ["u_shape"]
    assert cand.m["share"] < cfg["cup"]["u_min_share_in_zone"]


@pytest.mark.parametrize("seed", SEEDS)
def test_full_cosine_cup_accepted(cfg, seed):
    df, truth = make_series("cosine", tail="near", seed=seed)
    best, _, bars = analyze(df, cfg)
    assert best is not None and best.stage == "NEAR_PIVOT"
    assert best.m["share"] >= cfg["cup"]["u_min_share_in_zone"]
    _assert_bars(best, truth, keys="ch")
    _assert_bottom(best, bars, truth)
    assert abs(bars.high[best.a] / truth["rim_price"] - 1) <= TOL_PRICE  # flat rim: check by price


@pytest.mark.parametrize("seed", SEEDS)
def test_u_shape_score_anchors(cfg, seed):
    """scoring.u_shape_range: a straight V scores 0 and a half-sine U scores 1."""
    df, truth = make_series("v", tail="near", seed=seed)
    _, cands, _ = analyze(df, cfg)
    assert _true_candidate(cands, truth).components["u_shape"] == 0
    df, _ = make_series("halfsine", tail="near", seed=seed)
    best, _, _ = analyze(df, cfg)
    assert best.components["u_shape"] == 1


@pytest.mark.parametrize("seed", SEEDS)
def test_every_rule_evaluated(cfg, seed):
    """No early exit: a rejected candidate still carries every rule of its kind."""
    df, truth = make_series("v", tail="near", seed=seed)
    _, cands, _ = analyze(df, cfg)
    names = {r.name for r in _true_candidate(cands, truth).rules}
    assert {"left_rim_dominance", "prior_uptrend", "cup_length", "cup_depth", "rim_ratio",
            "no_interior_peak", "bottom_position", "u_shape", "handle_depth_min",
            "handle_depth_max", "handle_position", "handle_length_max",
            "handle_length_min"} <= names


@pytest.mark.parametrize("seed", SEEDS)
def test_left_rim_overhead_flagged(cfg, seed):
    """Right rim 5% under the left rim: the pivot has ~5.3% of overhead up to high[a]."""
    df, truth = make_series(ROUND, tail="near", rim_ratio=0.95, seed=seed)
    best, _, bars = analyze(df, cfg)
    assert best is not None and best.stage == "NEAR_PIVOT"
    overhead = best.m["left_rim_overhead_pct"]
    assert overhead == pytest.approx((bars.high[best.a] / best.levels["pivot"] - 1) * 100)
    assert overhead == pytest.approx((1 / 0.95 - 1) * 100, abs=1.0)
    assert overhead >= cfg["flags"]["left_rim_overhead_min_pct"]
    assert "left_rim_overhead" in best.flags


@pytest.mark.parametrize("seed", SEEDS)
def test_level_rims_not_flagged(cfg, seed):
    df, _ = make_series(ROUND, tail="near", seed=seed)
    best, _, _ = analyze(df, cfg)
    assert best.m["left_rim_overhead_pct"] < cfg["flags"]["left_rim_overhead_min_pct"]
    assert "left_rim_overhead" not in best.flags


def test_overhead_flag_is_informational(cfg):
    """Raising the flag threshold changes flags only, never the stage or the rules."""
    df, _ = make_series(ROUND, tail="near", rim_ratio=0.95, seed=SEEDS[0])
    flagged, _, _ = analyze(df, cfg)
    cfg["flags"]["left_rim_overhead_min_pct"] = 99
    plain, _, _ = analyze(df, cfg)
    assert "left_rim_overhead" in flagged.flags and "left_rim_overhead" not in plain.flags
    assert (flagged.stage, flagged.a, flagged.c, flagged.score) == (plain.stage, plain.a, plain.c, plain.score)


def test_relaxed_config_merges(cfg):
    from scanner.config import load_config
    relaxed = load_config("config.relaxed.yaml")
    assert relaxed["handle"]["max_days"] == 90 and relaxed["cup"]["max_days"] == 400
    assert relaxed["handle"]["min_days"] == cfg["handle"]["min_days"]  # untouched keys survive
