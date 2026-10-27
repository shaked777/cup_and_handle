"""Shared fixtures: config and a synthetic cup-and-handle generator."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.config import load_config  # noqa: E402

SEED = 7
UPTREND_BARS, UPTREND_RISE = 120, 0.40
CUP_BARS, CUP_DEPTH = 150, 0.25
HANDLE_BARS = 15
NOISE = 0.003          # Gaussian noise per bar on the close
WICK = 0.002           # high/low extend this far beyond max/min(open, close)
BASE_VOLUME = 1_000_000


@pytest.fixture
def cfg():
    return load_config()


def make_series(cup: str = "round", handle_drop: float = 0.06, tail: str = "near",
                recovery: float | None = None, extra_bars: int = 0, seed: int = SEED):
    """Synthetic daily OHLCV and the true a/b/c/h indices.

    cup: "round" (half a cosine period: steep sides, rounded bottom) or "v" (straight lines).
    tail: "near"     3 bars rising to a close 2% under the pivot
          "breakout" the same, then a close 1% above the pivot on 2x average volume
          "none"     the series ends at the handle low
    recovery: if set, stop inside the cup once it has recovered this share of its depth.
    extra_bars: random-walk bars appended after the tail (for look-ahead tests).
    """
    rng = np.random.default_rng(seed)
    up = 100 * (1 + UPTREND_RISE) ** (np.arange(UPTREND_BARS) / (UPTREND_BARS - 1))
    p0 = up[-1]
    t = np.arange(1, CUP_BARS + 1)
    if cup == "round":
        shape = np.cos(np.pi * (t / CUP_BARS - 0.5))          # 0 at rims, 1 at the bottom
    else:
        shape = 1 - np.abs(1 - 2 * t / CUP_BARS)
    cup_path = p0 * (1 - CUP_DEPTH * shape)
    a, b, c = UPTREND_BARS - 1, UPTREND_BARS - 1 + CUP_BARS // 2, UPTREND_BARS - 1 + CUP_BARS
    if recovery is not None:
        right = np.nonzero((t > CUP_BARS // 2) & (1 - shape >= recovery))[0][0]
        path = np.concatenate([up, cup_path[: right + 1]])
        truth = dict(a=a, b=b)
    else:
        handle = p0 * (1 - handle_drop * np.arange(1, HANDLE_BARS + 1) / HANDLE_BARS)
        path = np.concatenate([up, cup_path, handle])
        truth = dict(a=a, b=b, c=c, h=c + HANDLE_BARS)

    close = path * (1 + rng.normal(0, NOISE, len(path)))
    volume = BASE_VOLUME * (1 + 0.1 * rng.standard_normal(len(path)))
    if recovery is None:
        volume[c + 1:] *= 0.7                                   # handle volume dries up

    def ohlc(cl):
        op = np.concatenate([[cl[0]], cl[:-1]])
        return op, np.maximum(op, cl) * (1 + WICK), np.minimum(op, cl) * (1 - WICK)

    if tail in ("near", "breakout") and recovery is None:
        _, hi, _ = ohlc(close)
        pivot = hi[c - 5:c + 6].max()
        rise = np.linspace(close[-1], 0.98 * pivot, 4)[1:]
        close = np.concatenate([close, rise])
        volume = np.concatenate([volume, np.full(3, BASE_VOLUME * 0.8)])
        if tail == "breakout":
            close = np.concatenate([close, [1.01 * pivot]])
            volume = np.concatenate([volume, [2 * volume[-50:].mean()]])
        truth["pivot"] = pivot
    if extra_bars:
        walk = close[-1] * np.cumprod(1 + rng.normal(0, 0.02, extra_bars))
        close = np.concatenate([close, walk])
        volume = np.concatenate([volume, np.full(extra_bars, BASE_VOLUME)])

    op, hi, lo = ohlc(close)
    dates = pd.bdate_range("2023-01-02", periods=len(close))
    df = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close, "Volume": volume},
                      index=pd.DatetimeIndex(dates, name="Date"))
    return df, truth
