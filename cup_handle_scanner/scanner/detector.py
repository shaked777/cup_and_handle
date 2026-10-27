"""Cup-and-handle detection on daily bars 0..T (T = last completed bar).

Indices: a = left rim, b = cup bottom, c = right rim, h = handle low, e = breakout bar.
Every rule is evaluated for every candidate (no early exit) so --explain and the near-miss
list can show all failures. Every value is computed from bars 0..T only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .pivots import swing_highs

STAGES = ["BREAKOUT", "NEAR_PIVOT", "HANDLE_FORMING", "RIGHT_RIM", "CUP_FORMING"]
STAGE_RANK = {s: len(STAGES) - i for i, s in enumerate(STAGES)}  # BREAKOUT highest


@dataclass
class Bars:
    dates: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @property
    def T(self) -> int:
        return len(self.close) - 1

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "Bars":
        return cls(
            dates=df.index.values.astype("datetime64[D]"),
            open=df["Open"].to_numpy(float), high=df["High"].to_numpy(float),
            low=df["Low"].to_numpy(float), close=df["Close"].to_numpy(float),
            volume=df["Volume"].to_numpy(float),
        )

    def date(self, i: int | None) -> str:
        return "" if i is None else str(self.dates[i])


@dataclass
class Rule:
    name: str
    group: str
    value: float | None      # measured value (None when it cannot be measured)
    shown: str               # measured value as text
    threshold: str
    passed: bool | None      # None = not applicable to this candidate
    required: bool = False   # counts toward the candidate's target stage

    def __post_init__(self):
        if self.passed is not None:
            self.passed = bool(self.passed)  # numpy.bool_ would break `is False` checks
        if self.value is not None:
            self.value = float(self.value)

    def status(self) -> str:
        if self.passed is None:
            return "n/a"
        return "PASS" if self.passed else "FAIL"


@dataclass
class Candidate:
    kind: str                       # "cup" (a, b, c) or "forming" (a, b only)
    a: int
    b: int
    c: int | None = None
    h: int | None = None
    e: int | None = None
    c_confirmed: bool = True
    handle_start: int | None = None
    handle_end: int | None = None   # inclusive
    target_stage: str = ""
    stage: str | None = None        # set when every required rule passes
    rules: list[Rule] = field(default_factory=list)
    m: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)
    score: float = 0.0
    levels: dict = field(default_factory=dict)

    @property
    def failed(self) -> list[Rule]:
        return [r for r in self.rules if r.required and r.passed is False]

    @property
    def has_handle(self) -> bool:
        return self.kind == "cup" and self.h is not None and self.target_stage != "RIGHT_RIM"

    @property
    def recency(self) -> int:
        return self.c if self.c is not None else self.a


# ---------------------------------------------------------------- helpers

def _pct(v):
    return "n/a" if v is None else f"{v:.1f}%"


def _range(lo, hi, unit=""):
    return f"{lo}{unit} .. {hi}{unit}"


def _within(v, lo, hi):
    return v is not None and lo <= v <= hi


def _left_rim_rules(s: Bars, a: int, cfg: dict) -> list[Rule]:
    dom = cfg["cup"]["left_rim_dominance_days"]
    lo = max(0, a - dom)
    prior_max = float(s.high[lo:a].max()) if a > lo else None
    dom_ok = prior_max is None or s.high[a] >= prior_max
    rules = [Rule(
        "left_rim_dominance", "left_rim",
        None if prior_max is None else s.high[a] / prior_max,
        f"high[a] {s.high[a]:.2f} vs prior max {prior_max:.2f}" if prior_max else "no prior bars",
        f"highest high of prior {dom} bars", dom_ok)]

    pu = cfg["prior_uptrend"]
    lo = max(0, a - pu["lookback_days"])
    rise = (s.high[a] / s.low[lo:a].min() - 1) * 100 if a > lo else None
    rules.append(Rule(
        "prior_uptrend", "left_rim", rise, _pct(rise),
        f">= {pu['min_rise_pct']}% over {pu['lookback_days']} bars before a",
        rise is not None and rise >= pu["min_rise_pct"]))
    return rules


def share_in_zone(close: np.ndarray, a: int, end: int, bottom: float, depth: float, zone: float) -> float:
    """Share of closes in a..end at or below bottom + zone * depth (the round-not-V measure)."""
    return float(np.mean(close[a:end + 1] <= bottom + zone * depth))


def _u_shape_rule(s: Bars, a: int, end: int, b: int, depth: float, cfg: dict) -> tuple[Rule, float]:
    cc = cfg["cup"]
    share = share_in_zone(s.close, a, end, s.low[b], depth, cc["u_bottom_zone"])
    return Rule("u_shape", "cup", share,
                f"{share:.3f} of closes in bottom {cc['u_bottom_zone']:.0%} of depth",
                f">= {cc['u_min_share_in_zone']}", share >= cc["u_min_share_in_zone"]), share


# ---------------------------------------------------------------- cup with right rim

def _eval_cup(s: Bars, a: int, b: int, c: int, confirmed: bool, left: list[Rule], cfg: dict) -> Candidate:
    T = s.T
    cc, hc, bc = cfg["cup"], cfg["handle"], cfg["breakout"]
    cand = Candidate("cup", a, b, c, c_confirmed=confirmed)
    rules = [Rule(r.name, r.group, r.value, r.shown, r.threshold, r.passed) for r in left]

    pivot = float(s.high[c])
    depth = float(s.high[a] - s.low[b])
    depth_pct = depth / s.high[a] * 100
    cup_days = c - a
    rim = pivot / s.high[a]
    interior = float(s.high[a + 1:c].max()) if c - a > 1 else -np.inf
    rim_top = max(s.high[a], pivot)
    bottom_pos = (b - a) / cup_days
    u_rule, share = _u_shape_rule(s, a, c, b, depth, cfg)
    rules += [
        Rule("cup_length", "cup", cup_days, f"{cup_days} bars (c - a)",
             _range(cc["min_days"], cc["max_days"], " bars"), _within(cup_days, cc["min_days"], cc["max_days"])),
        Rule("cup_depth", "cup", depth_pct, _pct(depth_pct),
             _range(cc["min_depth_pct"], cc["max_depth_pct"], "%"),
             _within(depth_pct, cc["min_depth_pct"], cc["max_depth_pct"])),
        Rule("rim_ratio", "cup", rim, f"{rim:.3f} (high[c]/high[a])",
             _range(cc["rim_min_ratio"], cc["rim_max_ratio"]), _within(rim, cc["rim_min_ratio"], cc["rim_max_ratio"])),
        Rule("no_interior_peak", "cup", interior / rim_top if interior > 0 else 0.0,
             f"interior max {interior:.2f} vs rims {rim_top:.2f}" if interior > 0 else "no interior bars",
             "<= max(high[a], high[c])", bool(interior <= rim_top)),
        Rule("bottom_position", "cup", bottom_pos, f"{bottom_pos:.3f} ((b-a)/(c-a))",
             _range(cc["bottom_position_min"], cc["bottom_position_max"]),
             _within(bottom_pos, cc["bottom_position_min"], cc["bottom_position_max"])),
        u_rule,
    ]

    # Breakout: first close above the pivot after c, using bars up to T only.
    above = np.nonzero(s.close[c + 1:T + 1] > pivot)[0]
    e = c + 1 + int(above[0]) if len(above) else None
    hs, he = c + 1, (e - 1 if e is not None else T)
    hlen = max(0, he - hs + 1)
    if hlen:
        h = hs + int(np.argmin(s.low[hs:he + 1]))
        hdepth = (pivot - s.low[h]) / pivot * 100
        hpos = (s.low[h] - s.low[b]) / depth if depth > 0 else None
        cand.h, cand.handle_start, cand.handle_end = h, hs, he
    else:
        h = hdepth = hpos = None
    hmax_len = min(hc["max_days"], hc["max_days_vs_cup"] * cup_days)
    rules += [
        Rule("handle_depth_min", "handle", hdepth, _pct(hdepth) if hlen else "no handle bars",
             f">= {hc['min_depth_pct']}%", hdepth is not None and hdepth >= hc["min_depth_pct"]),
        Rule("handle_depth_max", "handle", hdepth, _pct(hdepth) if hlen else "no handle bars",
             f"<= {hc['max_depth_pct']}%", None if hdepth is None else hdepth <= hc["max_depth_pct"]),
        Rule("handle_position", "handle", hpos,
             "no handle bars" if hpos is None else f"{hpos:.3f} of cup depth above low[b]",
             f">= {hc['min_position_in_cup']}", None if hpos is None else hpos >= hc["min_position_in_cup"]),
        Rule("handle_length_max", "handle", hlen, f"{hlen} bars",
             f"<= {hmax_len:.0f} bars (min of {hc['max_days']}, {hc['max_days_vs_cup']} x cup)",
             hlen <= hmax_len),
        Rule("handle_length_min", "handle", hlen, f"{hlen} bars", f">= {hc['min_days']} bars",
             hlen >= hc["min_days"]),
    ]

    close_t = float(s.close[T])
    dist_pct = (close_t / pivot - 1) * 100
    vr = None
    if e is not None:
        cand.e = e
        lo = max(0, e - bc["volume_avg_days"])
        avg = float(s.volume[lo:e].mean()) if e > lo else 0.0
        vr = float(s.volume[e] / avg) if avg > 0 else None
        age = T - e
        after = s.close[e + 1:T + 1]
        min_after = float(after.min()) if len(after) else None
        rules += [
            Rule("breakout_recent", "breakout", age, f"{age} bars ago ({s.date(e)})",
                 f"< {bc['max_age_days']} bars ago", age < bc["max_age_days"]),
            Rule("breakout_not_failed", "breakout", min_after,
                 "no closes after breakout" if min_after is None else f"lowest close since {min_after:.2f}",
                 "no close below low[h]" + ("" if h is None else f" ({s.low[h]:.2f})"),
                 None if h is None else (min_after is None or min_after >= s.low[h])),
        ]
        if vr is not None and vr >= bc["volume_ratio_min"]:
            cand.flags.append("volume_confirmed")
        if close_t > pivot * (1 + bc["extended_pct"] / 100):
            cand.flags.append("extended")

    # Target stage and the rules it requires.
    by = {r.name: r for r in rules}
    req = [r.name for r in rules if r.group in ("left_rim", "cup")]
    if e is not None:
        target = "BREAKOUT"
        req += ["handle_depth_min", "handle_depth_max", "handle_position", "handle_length_max",
                "handle_length_min", "breakout_recent", "breakout_not_failed"]
    elif not confirmed or hdepth is None or hdepth < hc["min_depth_pct"]:
        target = "RIGHT_RIM"
        req += ["handle_depth_max", "handle_position", "handle_length_max"]
        if not confirmed:
            cand.flags.append("rim_unconfirmed")
    else:
        req += ["handle_depth_min", "handle_depth_max", "handle_position", "handle_length_max"]
        near = close_t >= pivot * (1 - bc["near_pivot_pct"] / 100)
        if hlen >= hc["min_days"] and near:
            target = "NEAR_PIVOT"
            req.append("handle_length_min")
        else:
            target = "HANDLE_FORMING"
    for name in req:
        by[name].required = True

    # Informational: a right rim below the left rim leaves resistance at high[a] above the pivot.
    overhead = (s.high[a] / pivot - 1) * 100
    if overhead >= cfg["flags"]["left_rim_overhead_min_pct"]:
        cand.flags.append("left_rim_overhead")

    cand.rules = rules
    cand.target_stage = target
    cand.stage = target if not cand.failed else None
    cand.m = dict(pivot=pivot, depth=depth, depth_pct=depth_pct, cup_days=cup_days, share=share,
                  rim_ratio=rim, handle_len=hlen, handle_depth_pct=hdepth, handle_pos=hpos,
                  close=close_t, dist_pct=dist_pct, volume_ratio=vr,
                  breakout_age=None if e is None else T - e, left_rim_overhead_pct=overhead)
    return cand


# ---------------------------------------------------------------- forming cup (no right rim yet)

def _eval_forming(s: Bars, a: int, left: list[Rule], cfg: dict) -> Candidate:
    T, k = s.T, cfg["pivots"]["swing_window"]
    cc = cfg["cup"]
    b = a + int(np.argmin(s.low[a:T + 1]))
    cand = Candidate("forming", a, b, c_confirmed=False)
    rules = [Rule(r.name, r.group, r.value, r.shown, r.threshold, r.passed) for r in left]

    pivot = float(s.high[a])
    depth = float(pivot - s.low[b])
    depth_pct = depth / pivot * 100
    span = T - a
    later_max = float(s.high[a + 1:T + 1].max()) if T > a else -np.inf
    since_b = float(s.high[b + 1:T + 1].max()) if T > b else float(s.high[b])
    close_t = float(s.close[T])
    recovery = (close_t - s.low[b]) / depth if depth > 0 else 0.0
    u_rule, share = _u_shape_rule(s, a, T, b, depth, cfg)
    rules += [
        Rule("forming_bottom_confirmed", "cup", T - b, f"bottom {T - b} bars before T",
             f">= {k} bars before T", b <= T - k),
        Rule("forming_no_higher_high", "cup", later_max / pivot if later_max > 0 else 0.0,
             f"max high after a {later_max:.2f} vs high[a] {pivot:.2f}", "<= high[a]",
             bool(later_max <= pivot)),
        Rule("cup_length", "cup", span, f"{span} bars (T - a)",
             _range(cc["min_days"], cc["max_days"], " bars"), _within(span, cc["min_days"], cc["max_days"])),
        Rule("cup_depth", "cup", depth_pct, _pct(depth_pct),
             _range(cc["min_depth_pct"], cc["max_depth_pct"], "%"),
             _within(depth_pct, cc["min_depth_pct"], cc["max_depth_pct"])),
        Rule("forming_recovery", "cup", recovery, f"{recovery:.3f} of depth recovered",
             f">= {cc['forming_min_recovery']}", recovery >= cc["forming_min_recovery"]),
        Rule("forming_below_rim", "cup", close_t / pivot, f"close {close_t / pivot:.3f} x high[a]",
             f"< {cc['rim_min_ratio']} x high[a]", close_t < cc["rim_min_ratio"] * pivot),
        # No right rim yet: once a high after b reaches the rim zone, the base is judged as a cup
        # with a right rim (and its handle), not as a forming cup.
        Rule("forming_no_right_rim", "cup", since_b / pivot,
             f"max high since b {since_b / pivot:.3f} x high[a]",
             f"< {cc['rim_min_ratio']} x high[a]", since_b < cc["rim_min_ratio"] * pivot),
        u_rule,
    ]
    for r in rules:
        r.required = True
    cand.rules = rules
    cand.target_stage = "CUP_FORMING"
    cand.stage = "CUP_FORMING" if not cand.failed else None
    cand.m = dict(pivot=pivot, depth=depth, depth_pct=depth_pct, cup_days=span, share=share,
                  rim_ratio=None, handle_len=0, handle_depth_pct=None, handle_pos=None,
                  close=close_t, dist_pct=(close_t / pivot - 1) * 100, volume_ratio=None,
                  breakout_age=None, recovery=recovery, left_rim_overhead_pct=None)
    return cand


# ---------------------------------------------------------------- entry points

def detect(s: Bars, cfg: dict) -> list[Candidate]:
    """Every candidate (accepted or not) for the bars 0..T in `s`."""
    T, k = s.T, cfg["pivots"]["swing_window"]
    highs = swing_highs(s.high, k, T)
    max_back = cfg["cup"]["max_days"] + cfg["handle"]["max_days"] + cfg["breakout"]["max_age_days"]
    cands = []
    for a in highs[highs >= T - max_back]:
        a = int(a)
        left = _left_rim_rules(s, a, cfg)
        for c in highs[highs > a]:
            c = int(c)
            b = a + int(np.argmin(s.low[a:c + 1]))
            cands.append(_eval_cup(s, a, b, c, True, left, cfg))
        b = a + int(np.argmin(s.low[a:T + 1]))
        if b < T:  # unconfirmed right rim: highest high since the bottom, inside the last k bars
            c = b + 1 + int(np.argmax(s.high[b + 1:T + 1]))
            if T - c < k:
                cands.append(_eval_cup(s, a, b, c, False, left, cfg))
        cands.append(_eval_forming(s, a, left, cfg))
    return cands


def format_flags(c: Candidate) -> str:
    """Flags for display; left_rim_overhead carries its percentage."""
    out = []
    for f in c.flags:
        if f == "left_rim_overhead":
            out.append(f"left_rim_overhead +{c.m['left_rim_overhead_pct']:.1f}%")
        else:
            out.append(f)
    return ", ".join(out)


def best_key(c: Candidate):
    return (STAGE_RANK[c.stage], c.score, c.recency)


def select_best(cands: list[Candidate]) -> Candidate | None:
    """Most advanced stage, then highest score, then most recent c (or a when forming)."""
    ok = [c for c in cands if c.stage]
    return max(ok, key=best_key) if ok else None


def explain_order(cands: list[Candidate]) -> list[Candidate]:
    """Accepted first (best first), then rejected by fewest failures, stage, score."""
    return sorted(cands, key=lambda c: (
        c.stage is not None,
        STAGE_RANK[c.stage] if c.stage else -len(c.failed),
        STAGE_RANK[c.target_stage],
        c.score,
        c.recency,
    ), reverse=True)


def best_near_miss(cands: list[Candidate]) -> Candidate | None:
    """The best rejected candidate that failed exactly one required rule."""
    nm = [c for c in cands if c.stage is None and len(c.failed) == 1]
    return max(nm, key=lambda c: (STAGE_RANK[c.target_stage], c.score, c.recency)) if nm else None
