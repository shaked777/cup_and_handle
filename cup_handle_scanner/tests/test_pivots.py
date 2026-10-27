from __future__ import annotations

import numpy as np

from scanner.pivots import swing_highs, swing_lows

K = 5


def test_simple_peak_and_trough():
    x = np.array([1, 2, 3, 4, 5, 9, 5, 4, 3, 2, 1, 2, 3], dtype=float)
    assert swing_highs(x, 2).tolist() == [5]
    y = -x
    assert swing_lows(y, 2).tolist() == [5]


def test_nothing_within_last_k_bars():
    rng = np.random.default_rng(1)
    high = 100 + np.cumsum(rng.normal(0, 1, 400))
    for T in range(50, 400, 7):
        hs = swing_highs(high, K, T)
        ls = swing_lows(high, K, T)
        assert (hs <= T - K).all() and (ls <= T - K).all()


def test_rising_series_has_no_confirmed_high_at_the_end():
    high = np.arange(50, dtype=float)
    assert swing_highs(high, K).size == 0


def test_prefix_equals_full_series_filtered():
    """Pivots at T use only bars 0..T: same as pivots on the full series confirmed by T."""
    rng = np.random.default_rng(2)
    high = 100 + np.cumsum(rng.normal(0, 1, 300))
    full = swing_highs(high, K)
    for T in range(20, 300, 5):
        expect = full[full <= T - K]
        assert swing_highs(high[: T + 1], K).tolist() == expect.tolist()
        assert swing_highs(high, K, T).tolist() == expect.tolist()
