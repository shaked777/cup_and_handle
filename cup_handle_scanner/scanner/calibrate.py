"""Calibrate the round-not-V rule: cup.u_bottom_zone and cup.u_min_share_in_zone.

    python -m scanner.calibrate [--config PATH]

For each bottom-zone size in calibration.zones, print the share of closes in a..c that sit at or
below low[b] + zone * depth for:
  - synthetic straight-line V, full-cosine U and half-sine U cups (calibration.seeds noisy seeds each)
  - the reference real cups in calibration.reference_file (EID Parry and earlier scan results)
then the gap between the highest V share and the lowest share that must pass, and a threshold in
that gap. A zone is usable only if every V seed falls below every cup that must pass.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
from datetime import date

import numpy as np

from . import data
from .config import load_config, resolve_path
from .detector import Bars, share_in_zone
from .synthetic import make_series

log = logging.getLogger("scanner.calibrate")

SYNTHETIC = [("v", "straight-line V"), ("cosine", "full-cosine U"), ("halfsine", "half-sine U")]
REJECT = {"v"}  # synthetic shapes that must fail the rule; everything else must pass


def _shares(bars: Bars, a: int, end: int, zones: list[float]) -> list[float]:
    b = a + int(np.argmin(bars.low[a:end + 1]))
    depth = bars.high[a] - bars.low[b]
    return [share_in_zone(bars.close, a, end, bars.low[b], depth, z) for z in zones]


def synthetic_shares(shape: str, zones: list[float], seeds: int) -> np.ndarray:
    """Array [seed, zone] of shares, measured on the true rims a..c."""
    rows = []
    for seed in range(seeds):
        df, truth = make_series(shape=shape, tail="near", seed=seed)
        rows.append(_shares(Bars.from_frame(df), truth["a"], truth["c"], zones))
    return np.array(rows)


def reference_shares(cfg: dict, zones: list[float]) -> list[tuple[str, list[float]]]:
    path = resolve_path(cfg["calibration"]["reference_file"])
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            window = data.history_window(cfg, date.fromisoformat(row["as_of"]))
            frames, skipped = data.load_prices([row["ticker"]], cfg, window)
            if row["ticker"] not in frames:
                raise RuntimeError(f"calibration: no data for {row['ticker']}: {skipped}")
            bars = Bars.from_frame(frames[row["ticker"]])
            index = {str(d): i for i, d in enumerate(bars.dates)}
            a = index[row["a"]]
            end = index[row["c"]] if row["c"] else bars.T
            label = row["label"] + ("" if row["c"] else " (forming, a..T)")
            out.append((label, _shares(bars, a, end, zones)))
    return out


def _floor(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def _ceil(x: float, step: float) -> float:
    return math.ceil(x / step - 1e-9) * step


def calibrate(cfg: dict) -> dict:
    zones = [float(z) for z in cfg["calibration"]["zones"]]
    seeds = int(cfg["calibration"]["seeds"])
    synth = {shape: synthetic_shares(shape, zones, seeds) for shape, _ in SYNTHETIC}
    refs = reference_shares(cfg, zones)
    summary = []
    for j, z in enumerate(zones):
        max_reject = max(synth[s][:, j].max() for s in REJECT)
        keep = [synth[s][:, j].min() for s, _ in SYNTHETIC if s not in REJECT] + [r[1][j] for r in refs]
        min_keep = min(keep)
        summary.append({"zone": z, "max_v": max_reject, "min_keep": min_keep, "gap": min_keep - max_reject,
                        "threshold": round((max_reject + min_keep) / 2, 2) if min_keep > max_reject else None,
                        "score_range": (round(_ceil(synth["v"][:, j].max(), 0.01), 2),
                                        round(_floor(synth["halfsine"][:, j].min(), 0.01), 2))})
    return {"zones": zones, "seeds": seeds, "synth": synth, "refs": refs, "summary": summary}


def print_table(res: dict) -> None:
    zones = res["zones"]
    w = 20
    print(f"\nShare of closes in the bottom zone (low[b] + zone x depth), a..c; "
          f"synthetic rows: mean [min-max] over {res['seeds']} seeds\n")
    print(f"{'cup':<28}" + "".join(f"{'zone ' + format(z, '.2f'):>{w}}" for z in zones))
    for shape, label in SYNTHETIC:
        arr = res["synth"][shape]
        cells = [f"{arr[:, j].mean():.3f} [{arr[:, j].min():.2f}-{arr[:, j].max():.2f}]" for j in range(len(zones))]
        print(f"{label + (' (reject)' if shape in REJECT else ''):<28}" + "".join(f"{c:>{w}}" for c in cells))
    for label, vals in res["refs"]:
        print(f"{label:<28}" + "".join(f"{v:>{w}.3f}" for v in vals))
    print("-" * (28 + w * len(zones)))
    rows = [("highest V share", lambda s: f"{s['max_v']:.3f}"),
            ("lowest must-pass share", lambda s: f"{s['min_keep']:.3f}"),
            ("gap", lambda s: f"{s['gap']:+.3f}"),
            ("threshold (mid-gap)", lambda s: "none" if s["threshold"] is None else f"{s['threshold']:.2f}"),
            ("score range V..half-sine", lambda s: f"{s['score_range'][0]:.2f}..{s['score_range'][1]:.2f}")]
    for label, fn in rows:
        print(f"{label:<28}" + "".join(f"{fn(s):>{w}}" for s in res["summary"]))
    usable = [s for s in res["summary"] if s["threshold"] is not None]
    if not usable:
        print("\nNo zone separates every V seed from every cup that must pass.")
        return
    best = max(usable, key=lambda s: s["gap"])
    print(f"\nWidest gap: zone {best['zone']:.2f}, threshold {best['threshold']:.2f} "
          f"(V max {best['max_v']:.3f}, must-pass min {best['min_keep']:.3f}); "
          f"u_shape_range {list(best['score_range'])}")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m scanner.calibrate", description=__doc__.split("\n")[0])
    p.add_argument("--config", help="YAML file deep-merged over config.yaml")
    args = p.parse_args(argv)
    print_table(calibrate(load_config(args.config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
