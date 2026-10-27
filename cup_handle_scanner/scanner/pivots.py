"""Confirmed swing highs and lows.

A swing high at bar i: high[i] is the max of high[i-k .. i+k]. It is reported only when
i + k <= T (the k bars after it exist), so it never depends on bars after T.
"""
from __future__ import annotations

import numpy as np


def _swings(values: np.ndarray, k: int, T: int | None, is_high: bool) -> np.ndarray:
    T = len(values) - 1 if T is None else T
    x = np.asarray(values[: T + 1], dtype=float)
    n = len(x)
    if n < 2 * k + 1:
        return np.empty(0, dtype=int)
    windows = np.lib.stride_tricks.sliding_window_view(x, 2 * k + 1)
    centre = x[k: n - k]
    ext = windows.max(axis=1) if is_high else windows.min(axis=1)
    return np.nonzero(centre == ext)[0] + k  # centre index i satisfies k <= i <= T - k


def swing_highs(high: np.ndarray, k: int, T: int | None = None) -> np.ndarray:
    """Indices of confirmed swing highs using bars 0..T only."""
    return _swings(high, k, T, is_high=True)


def swing_lows(low: np.ndarray, k: int, T: int | None = None) -> np.ndarray:
    """Indices of confirmed swing lows using bars 0..T only."""
    return _swings(low, k, T, is_high=False)
