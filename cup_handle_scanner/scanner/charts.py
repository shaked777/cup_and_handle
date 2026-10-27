"""Annotated candlestick chart per reported ticker (mplfinance, Agg backend)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .detector import Bars, Candidate  # noqa: E402

COLOR = {"a": "#1565c0", "b": "#6a1b9a", "c": "#1565c0", "h": "#e65100", "e": "#2e7d32",
         "pivot": "#1565c0", "stop": "#c62828", "target": "#2e7d32", "curve": "#6a1b9a",
         "handle": "#ff9800"}


def _label(ax, x, y, text, color, above, fs):
    ax.annotate(text, xy=(x, y), xytext=(0, 34 if above else -34), textcoords="offset points",
                ha="center", va="center", fontsize=fs + 2, fontweight="bold", color=color,
                bbox=dict(boxstyle="circle,pad=0.25", fc="white", ec=color, lw=2),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=2), zorder=6)


def _hline(ax, y, text, color, style, fs):
    """Horizontal level with a price tag in the left margin (the price axis is on the right)."""
    ax.axhline(y, color=color, ls=style, lw=2, zorder=4)
    ax.annotate(f"{text}\n{y:,.2f}", xy=(0, y), xycoords=("axes fraction", "data"),
                xytext=(-6, 0), textcoords="offset points", ha="right", va="center",
                color="white", fontsize=fs - 2, fontweight="bold", annotation_clip=False,
                bbox=dict(boxstyle="round,pad=0.3", fc=color, ec="none"))


def caption(cand: Candidate) -> str:
    m, lv = cand.m, cand.levels
    lines = [f"depth {m['depth_pct']:.1f}%  ·  cup {m['cup_days'] / 5:.1f} wk"]
    if cand.has_handle:
        lines.append(f"handle {m['handle_depth_pct']:.1f}% deep, {m['handle_len']} days")
    else:
        lines.append("no handle yet")
    lines.append(f"{lv['distance_to_pivot_pct']:+.1f}% to pivot")
    return "\n".join(lines)


def render_chart(ticker: str, df: pd.DataFrame, bars: Bars, cand: Candidate, cfg: dict,
                 path: Path, company: str = "") -> Path:
    rc = cfg["report"]
    fs = rc["chart_font_size"]
    dpi = rc["chart_dpi"]
    T = bars.T
    start = max(0, cand.a - rc["chart_padding_days"])
    sub = df.iloc[start:T + 1]
    n = len(sub)

    style = mpf.make_mpf_style(base_mpf_style="yahoo", rc={
        "font.size": fs, "axes.labelsize": fs, "xtick.labelsize": fs - 2, "ytick.labelsize": fs - 2})
    fig, axes = mpf.plot(sub, type="candle", volume=True, style=style, returnfig=True,
                         figsize=(rc["chart_width_px"] / dpi, rc["chart_height_px"] / dpi),
                         panel_ratios=(4, 1), datetime_format="%Y-%m-%d", xrotation=0,
                         scale_padding=dict(left=0.55, right=0.55, top=0.6, bottom=0.5),
                         warn_too_much_data=len(sub) + 1)
    ax, axv = axes[0], axes[2]

    def x(i):
        return i - start

    lv = cand.levels
    end = cand.c if cand.c is not None else T

    # Quadratic fitted to the lows of the cup.
    xs = np.arange(cand.a, end + 1)
    coef = np.polyfit(xs - cand.a, bars.low[cand.a:end + 1], 2)
    ax.plot(xs - start, np.polyval(coef, xs - cand.a), color=COLOR["curve"], lw=3, alpha=0.7, zorder=5)

    if cand.has_handle:
        for a_ in (ax, axv):
            a_.axvspan(x(cand.handle_start) - 0.5, x(cand.handle_end) + 0.5,
                       color=COLOR["handle"], alpha=0.15, zorder=0)

    _hline(ax, lv["pivot"], "pivot", COLOR["pivot"], "--", fs)
    _hline(ax, lv["stop"], "stop", COLOR["stop"], "-.", fs)
    _hline(ax, lv["target"], "target", COLOR["target"], ":", fs)

    _label(ax, x(cand.a), bars.high[cand.a], "a", COLOR["a"], True, fs)
    _label(ax, x(cand.b), bars.low[cand.b], "b", COLOR["b"], False, fs)
    if cand.c is not None:
        _label(ax, x(cand.c), bars.high[cand.c], "c", COLOR["c"], True, fs)
    if cand.has_handle:
        _label(ax, x(cand.h), bars.low[cand.h], "h", COLOR["h"], False, fs)
    if cand.e is not None:
        _label(ax, x(cand.e), bars.high[cand.e], "e", COLOR["e"], True, fs)

    lo = min(sub["Low"].min(), lv["stop"])
    hi = max(sub["High"].max(), lv["target"], lv["pivot"])
    pad = (hi - lo) * 0.14  # room for the a/b/c/h labels
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, n + 1)

    head = f"{ticker}  {cand.stage}  score {cand.score:.0f}"
    ax.text(0.01, 0.97, head + "\n" + caption(cand), transform=ax.transAxes, va="top", ha="left",
            fontsize=fs + 1, linespacing=1.4, zorder=7,
            bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#9e9e9e", alpha=0.9))
    title = f"{ticker}{' - ' + company if company else ''}   daily, last bar {bars.date(T)}"
    ax.set_title(title, fontsize=fs + 3, loc="left", fontweight="bold")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path
