"""Synthetic daily OHLCV with a known cup and handle, for tests and calibration.

Layout: a 120-bar prior uptrend (+40%), a 150-bar cup 25% deep, a 15-bar handle drifting 6%
lower, Gaussian noise of 0.3% per bar on the close. These constants define the test data; they are
not detector thresholds (those live in config.yaml).

Cup shapes, as the share of the depth below the rim at bar t of n:
  "v"         straight lines down and up
  "cosine"    one full cosine period: flat at the rims and at the bottom
  "halfsine"  half a sine period: steep sides, rounded bottom
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SHAPES = ("v", "cosine", "halfsine")

UPTREND_BARS, UPTREND_RISE = 120, 0.40
CUP_BARS, CUP_DEPTH = 150, 0.25
HANDLE_BARS = 15
NOISE = 0.003          # Gaussian noise per bar on the close
WICK = 0.002           # high/low extend this far beyond max/min(open, close)
BASE_VOLUME = 1_000_000
HANDLE_VOLUME = 0.7    # handle volume as a share of the base (volume dries up)
TAIL_BARS = 3          # bars rising from the handle low before the final close
NEAR_CLOSE = 0.98      # "near" tail ends 2% under the pivot
BREAKOUT_CLOSE = 1.01  # breakout close 1% above the pivot
BREAKOUT_VOLUME = 2.0  # breakout volume vs the average of the 50 bars before it
VOLUME_AVG_BARS = 50
EXTRA_NOISE = 0.02     # daily noise of the random walk appended by extra_bars


def cup_depth_share(shape: str, t: np.ndarray, n: int) -> np.ndarray:
    """Share of the depth below the rim: 0 at both rims, 1 at the bottom."""
    if shape == "v":
        return 1 - np.abs(1 - 2 * t / n)
    if shape == "cosine":
        return (1 - np.cos(2 * np.pi * t / n)) / 2
    if shape == "halfsine":
        return np.sin(np.pi * t / n)
    raise ValueError(f"unknown cup shape {shape!r}; expected one of {SHAPES}")


def _ohlc(close: np.ndarray):
    op = np.concatenate([[close[0]], close[:-1]])
    return op, np.maximum(op, close) * (1 + WICK), np.minimum(op, close) * (1 - WICK)


def make_series(shape: str = "cosine", handle_drop: float = 0.06, tail: str = "near",
                recovery: float | None = None, extra_bars: int = 0, rim_ratio: float = 1.0,
                seed: int = 0) -> tuple[pd.DataFrame, dict]:
    """Synthetic OHLCV and the truth: a, b, c, h indices, bottom_price and pivot.

    tail: "near"     3 bars rising to a close 2% under the pivot
          "breakout" the same, then a close 1% above the pivot on 2x average volume
          "none"     the series ends with the handle
    recovery: stop inside the cup once it has recovered this share of its depth (no right rim).
    extra_bars: random-walk bars appended at the end (for look-ahead tests).
    rim_ratio: right rim as a multiple of the left rim (1.0 = level rims).
    """
    rng = np.random.default_rng(seed)
    up = 100 * (1 + UPTREND_RISE) ** (np.arange(UPTREND_BARS) / (UPTREND_BARS - 1))
    p0 = up[-1]
    bottom = p0 * (1 - CUP_DEPTH)
    t = np.arange(1, CUP_BARS + 1)
    share = cup_depth_share(shape, t, CUP_BARS)
    right_rim = p0 * rim_ratio
    cup = np.where(t <= CUP_BARS // 2, p0 - (p0 - bottom) * share, right_rim - (right_rim - bottom) * share)
    a, b, c = UPTREND_BARS - 1, UPTREND_BARS - 1 + CUP_BARS // 2, UPTREND_BARS - 1 + CUP_BARS
    # Prices the cup would show without noise: its lowest bar low and its left-rim bar high.
    truth = {"a": a, "b": b, "bottom_price": bottom * (1 - WICK), "rim_price": p0 * (1 + WICK)}

    if recovery is not None:
        right = np.nonzero((t > CUP_BARS // 2) & (1 - share >= recovery))[0][0]
        path = np.concatenate([up, cup[: right + 1]])
    else:
        handle = right_rim * (1 - handle_drop * np.arange(1, HANDLE_BARS + 1) / HANDLE_BARS)
        path = np.concatenate([up, cup, handle])
        truth.update(c=c, h=c + HANDLE_BARS)

    close = path * (1 + rng.normal(0, NOISE, len(path)))
    volume = BASE_VOLUME * (1 + 0.1 * rng.standard_normal(len(path)))
    if recovery is None:
        volume[c + 1:] *= HANDLE_VOLUME

    if tail in ("near", "breakout") and recovery is None:
        _, hi, _ = _ohlc(close)
        pivot = hi[c - 5:c + 6].max()
        rise = np.linspace(close[-1], NEAR_CLOSE * pivot, TAIL_BARS + 1)[1:]
        close = np.concatenate([close, rise])
        volume = np.concatenate([volume, np.full(TAIL_BARS, BASE_VOLUME * HANDLE_VOLUME)])
        if tail == "breakout":
            close = np.concatenate([close, [BREAKOUT_CLOSE * pivot]])
            volume = np.concatenate([volume, [BREAKOUT_VOLUME * volume[-VOLUME_AVG_BARS:].mean()]])
        truth["pivot"] = pivot
    if extra_bars:
        walk = close[-1] * np.cumprod(1 + rng.normal(0, EXTRA_NOISE, extra_bars))
        close = np.concatenate([close, walk])
        volume = np.concatenate([volume, np.full(extra_bars, BASE_VOLUME)])

    op, hi, lo = _ohlc(close)
    dates = pd.bdate_range("2023-01-02", periods=len(close))
    df = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": volume},
                      index=pd.DatetimeIndex(dates, name="Date"))
    return df, truth
