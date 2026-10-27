"""0-100 score, trend and relative strength, and price levels per candidate."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .detector import Bars, Candidate


def _ramp(x: float, x0: float, x1: float) -> float:
    """0 at x0, 1 at x1, linear in between, clipped."""
    if x1 == x0:
        return float(x >= x1)
    return float(min(1.0, max(0.0, (x - x0) / (x1 - x0))))


def sma(values: np.ndarray, n: int) -> float | None:
    return float(values[-n:].mean()) if len(values) >= n else None


def trend_component(close: np.ndarray, cfg: dict) -> float:
    sc = cfg["scoring"]
    fast, slow = sma(close, sc["sma_fast_days"]), sma(close, sc["sma_slow_days"])
    v = 0.0
    if fast is not None and close[-1] > fast:
        v += 0.5
    if fast is not None and slow is not None and fast > slow:
        v += 0.5
    return v


def relative_strength(closes: dict[str, np.ndarray], cfg: dict) -> dict[str, float]:
    """Percentile rank (0 worst .. 1 best) of the rs_lookback_days return among scanned tickers."""
    sc = cfg["scoring"]
    n = sc["rs_lookback_days"]
    rets = {t: c[-1] / c[-1 - n] - 1 for t, c in closes.items() if len(c) > n}
    if len(rets) < max(2, sc["rs_min_universe"]):
        return {t: sc["no_data_value"] for t in closes}
    ranks = pd.Series(rets).rank(method="average")
    pct = (ranks - 1) / (len(ranks) - 1)
    return {t: float(pct[t]) if t in pct.index else sc["no_data_value"] for t in closes}


def score_candidate(cand: Candidate, s: Bars, cfg: dict, trend: float, rs: float) -> None:
    sc, cc = cfg["scoring"], cfg["cup"]
    nd = sc["no_data_value"]
    m = cand.m
    comp = {"u_shape": _ramp(m["share"], *sc["u_shape_range"])}

    if cand.kind == "forming":
        comp["symmetry"] = nd
    else:
        comp["symmetry"] = 1 - min(1.0, abs(math.log(s.high[cand.c] / s.high[cand.a])) / sc["symmetry_tolerance"])

    d = m["depth_pct"]
    lo, hi = sc["depth_sweet_spot_pct"]
    if lo <= d <= hi:
        comp["depth"] = 1.0
    elif d < lo:
        comp["depth"] = _ramp(d, cc["min_depth_pct"], lo)
    else:
        comp["depth"] = 1 - _ramp(d, hi, cc["max_depth_pct"])

    if cand.has_handle:
        hlo, hhi = sc["handle_sweet_spot_pct"]
        in_sweet = hlo <= m["handle_depth_pct"] <= hhi
        upper = s.low[cand.h] >= s.low[cand.b] + (1 - sc["handle_upper_zone"]) * m["depth"]
        comp["handle"] = 1.0 if in_sweet and upper else (0.5 if in_sweet or upper else 0.0)
        n = cfg["breakout"]["volume_avg_days"]
        lo_i = max(0, cand.c - n)
        base = float(s.volume[lo_i:cand.c].mean()) if cand.c > lo_i else 0.0
        hvol = float(s.volume[cand.handle_start:cand.handle_end + 1].mean())
        r = hvol / base if base > 0 else None
        m["handle_volume_ratio"] = r
        comp["volume"] = nd if r is None else 1 - _ramp(r, *sc["volume_dryup_range"])
    else:
        comp["handle"] = nd
        comp["volume"] = nd
        m["handle_volume_ratio"] = None

    comp["trend"] = trend
    comp["rel_strength"] = rs
    cand.components = comp
    cand.score = float(sum(sc["weights"][k] * v for k, v in comp.items()))


def compute_levels(cand: Candidate, s: Bars, cfg: dict) -> None:
    T = s.T
    pivot = cand.m["pivot"]
    if cand.has_handle:
        stop = float(s.low[cand.h])
    else:
        stop = float(s.low[max(0, T - cfg["levels"]["stop_lookback_days"] + 1):T + 1].min())
    depth = cand.m["depth"]
    method = cfg["targets"]["method"]
    if method == "absolute":
        target = pivot + depth
    elif method == "percent":
        target = pivot * (1 + depth / s.low[cand.b])
    else:
        raise ValueError(f"targets.method must be 'absolute' or 'percent', got {method!r}")
    cand.levels = {"pivot": pivot, "distance_to_pivot_pct": (s.close[T] / pivot - 1) * 100,
                   "stop": stop, "target": float(target)}


def score_all(cands: list[Candidate], s: Bars, cfg: dict, rs: float) -> None:
    trend = trend_component(s.close, cfg)
    for c in cands:
        score_candidate(c, s, cfg, trend, rs)
        compute_levels(c, s, cfg)
