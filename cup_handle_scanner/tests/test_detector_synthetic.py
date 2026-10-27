"""Detector on synthetic series: 120-bar +40% uptrend, 150-bar cosine cup 25% deep,
15-bar handle drifting 6% lower, 0.3% Gaussian noise per bar, fixed seed.

Index tolerance is 5 bars. a, c and h land within 2 bars for every seed tried (0..59);
b sits on a rounded bottom that is flat to ~0.1% over +-6 bars, so with 0.3% noise it can
drift ~6 bars on some seeds. The fixed seed is inside the tolerance.
"""
from __future__ import annotations

import pytest

from conftest import make_series
from scanner.cli import analyze

TOL = 5


def _near_truth(cand, truth, keys="abch"):
    return all(abs(getattr(cand, k) - truth[k]) <= TOL for k in keys if k in truth)


def _true_candidate(cands, truth):
    """The candidate whose a and c sit on the true rims."""
    hits = [c for c in cands if c.kind == "cup"
            and abs(c.a - truth["a"]) <= TOL and abs(c.c - truth["c"]) <= TOL]
    assert hits, "no candidate on the true rims"
    return max(hits, key=lambda c: c.score)


def test_near_pivot(cfg):
    df, truth = make_series(tail="near")
    best, _, _ = analyze(df, cfg)
    assert best is not None and best.stage == "NEAR_PIVOT"
    assert _near_truth(best, truth), {k: (getattr(best, k), truth[k]) for k in "abch"}
    assert best.levels["distance_to_pivot_pct"] == pytest.approx(-2.0, abs=0.3)
    assert best.levels["stop"] == pytest.approx(df["Low"].iloc[best.h])


def test_breakout_with_volume(cfg):
    df, truth = make_series(tail="breakout")
    best, _, _ = analyze(df, cfg)
    assert best is not None and best.stage == "BREAKOUT"
    assert best.e == len(df) - 1
    assert "volume_confirmed" in best.flags
    assert best.m["volume_ratio"] >= cfg["breakout"]["volume_ratio_min"]
    assert "extended" not in best.flags
    assert _near_truth(best, truth)


def test_v_cup_rejected_by_round_rule(cfg):
    df, truth = make_series(cup="v", tail="near")
    best, cands, _ = analyze(df, cfg)
    assert best is None
    cand = _true_candidate(cands, truth)
    assert [r.name for r in cand.failed] == ["u_shape"]
    assert cand.m["share"] < cfg["cup"]["u_min_share_in_zone"]


def test_round_cup_passes_round_rule(cfg):
    df, truth = make_series(tail="near")
    _, cands, _ = analyze(df, cfg)
    cand = _true_candidate(cands, truth)
    assert cand.m["share"] >= 0.5  # a rounded U spends half its time near the bottom


def test_deep_handle_rejected(cfg):
    df, truth = make_series(handle_drop=0.20, tail="none")
    best, cands, _ = analyze(df, cfg)
    assert best is None
    failed = {r.name for r in _true_candidate(cands, truth).failed}
    assert "handle_depth_max" in failed


def test_cup_forming(cfg):
    df, truth = make_series(recovery=0.55)
    best, _, _ = analyze(df, cfg)
    assert best is not None and best.stage == "CUP_FORMING"
    assert best.kind == "forming" and best.c is None
    assert _near_truth(best, truth, keys="ab")
    assert best.m["recovery"] >= cfg["cup"]["forming_min_recovery"]
    assert best.levels["pivot"] == pytest.approx(df["High"].iloc[best.a])


def test_every_rule_evaluated(cfg):
    """No early exit: a rejected candidate still carries every rule of its kind."""
    df, truth = make_series(cup="v", tail="near")
    _, cands, _ = analyze(df, cfg)
    cand = _true_candidate(cands, truth)
    names = {r.name for r in cand.rules}
    assert {"left_rim_dominance", "prior_uptrend", "cup_length", "cup_depth", "rim_ratio",
            "no_interior_peak", "bottom_position", "u_shape", "handle_depth_min",
            "handle_depth_max", "handle_position", "handle_length_max",
            "handle_length_min"} <= names


def test_relaxed_config_merges(cfg):
    from scanner.config import load_config
    relaxed = load_config("config.relaxed.yaml")
    assert relaxed["handle"]["max_days"] == 90 and relaxed["cup"]["max_days"] == 400
    assert relaxed["handle"]["min_days"] == cfg["handle"]["min_days"]  # untouched keys survive
