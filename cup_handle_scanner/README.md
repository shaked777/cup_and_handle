# Cup-and-handle scanner (S&P 500 / Nasdaq-100)

Scans the current S&P 500 (default) or Nasdaq-100 constituents on daily bars, finds
cup-and-handle patterns (a fresh breakout, or a pattern still forming) and writes a ranked report
with annotated charts.

Everything is free: Yahoo Finance daily bars through `yfinance`, the constituents table from
Wikipedia, open-source Python libraries. No API keys, no paid services, no AI calls.

Each run scans once, on the latest data (or an `--as-of` date). `scanner.cli.main()` is the single
non-interactive entry point, so a scheduler can call it later.

## Setup

Python 3.10 or newer.

```bash
cd cup_handle_scanner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

## Usage

```bash
.venv/bin/python -m scanner.cli [--config PATH] [--universe sp500|nasdaq100] [--tickers A,B] [--refresh] [--as-of YYYY-MM-DD] [--explain TICKER]
```

| Option | What it does |
|---|---|
| *(none)* | Scan the active universe (`universe.active`, default `sp500`) on the latest completed session and write the report. |
| `--config PATH` | YAML file deep-merged over `config.yaml`. It only needs the keys it changes (see `config.relaxed.yaml`). |
| `--universe NAME` | Scan `sp500` or `nasdaq100`, overriding `universe.active`. |
| `--tickers A,B` | Scan only these symbols. They may be outside the index (e.g. `EIDPARRY.NS`). Company names and sectors come from the active universe's saved list. |
| `--refresh` | Ignore the cache and download again. |
| `--as-of YYYY-MM-DD` | End the data window at that date. Nothing after it is downloaded or used. |
| `--explain TICKER` | Scan only that ticker and print its top candidates with every rule, the measured value and pass/fail. Writes no report files. |

Examples:

```bash
.venv/bin/python -m scanner.cli                                  # S&P 500
.venv/bin/python -m scanner.cli --universe nasdaq100
.venv/bin/python -m scanner.cli --tickers AAPL,MSFT,NVDA
.venv/bin/python -m scanner.cli --explain EIDPARRY.NS --as-of 2024-05-31 --config config.relaxed.yaml
.venv/bin/python -m scanner.calibrate                            # round-not-V calibration table
```

In `--explain` output, a status in capitals (PASS/FAIL) is required for that candidate's stage;
lower case is informational (for example the handle-depth minimum while a cup is still at its
right rim).

## Universes

| Name | Source | Ticker column | Sector column | Expected count | Benchmark |
|---|---|---|---|---|---|
| `sp500` (default) | [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) | Symbol | GICS Sector | 495..510 | SPY |
| `nasdaq100` | [List of NASDAQ-100 companies](https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies) | Ticker | ICB Industry (this table has no GICS) | 95..105 | QQQ |

- **Normalization.** Symbols are upper-cased, with `.` replaced by `-` as Yahoo expects (BRK.B -> BRK-B).
- **Count check.** The run fails loudly if the count is outside the expected range.
- **Saved lists.** Each list is saved to `data/universe_<name>.csv` (ticker, company, sector). If the
  fetch fails, that file is used with a warning.
- **Relative strength** is a percentile rank within the tickers of the run, which is the active
  universe on a full scan. A `--tickers` run ranks within its own list, or uses `no_data_value` if
  it has fewer than `scoring.rs_min_universe` tickers.
- **Price cache.** The per-ticker cache is shared between universes, so names already downloaded for
  one universe are reused by the other.

## Output

| Run | Folder |
|---|---|
| Full scan | `output/<universe>/<last session date>/`, e.g. `output/sp500/2026-10-26/` |
| `--tickers` | `output/custom/<last session date>/`, so a small run never overwrites a full scan |

Only real scans write to `output/`; tests write to pytest's `tmp_path`. `output/` is not committed.

| File | Contents |
|---|---|
| `report.html` | Self-contained and mobile-friendly; charts are embedded for the top `report.max_charts` results. Header: run time (Asia/Jerusalem), last session (New York), scanned/skipped counts, the benchmark against its SMA50/SMA200, a results-by-sector-and-stage table, key config values. Then the summary table (with Sector and Left rim overhead % columns), one section per stage sorted by score with chart and full rule list, near misses, skipped tickers. A warning is logged if the file exceeds `report.max_html_mb`. |
| `report.md` | The same report for GitHub, linking `charts/*.png`. |
| `results.csv` | One row per result: the summary-table columns plus the a/b/c/h/e dates. |
| `results.json` | Results with every rule, score component and flag, plus sector counts, near misses, skipped tickers and market context. |
| `charts/*.png` | 1600x900 chart for each of the top `report.max_charts` results (ordered by stage priority, then score). Each shows candles and volume, a/b/c/h (and breakout e) markers, a quadratic fitted to the cup lows, a dashed pivot line, stop and target lines, a thin left-rim line when flagged, the shaded handle and a caption box. |

Results beyond `report.max_charts` still appear in the tables and stage sections with all their
measurements, just without a chart. The console prints counts per stage and the top results.

## How detection works

Indices: **a** left rim, **b** cup bottom, **c** right rim, **h** handle low, **e** breakout bar.
T is the last completed bar. Every value is computed from bars 0..T only.

1. **Pivots.** A swing high at bar i is the highest high of bars i-k..i+k (k = `pivots.swing_window`). It
   exists only once i+k <= T. Swing lows work the same way with lows.
2. **Candidates.** Every confirmed swing high within the search window is a possible left rim a. It is
   paired with every later confirmed swing high c, with one unconfirmed right rim (the highest high
   since the bottom, if it is within the last k bars), and with a "forming cup" that has no right
   rim yet.
3. **Rules.** Every rule is evaluated for every candidate, with no early exit. The rules are the left
   rim (dominance, prior uptrend), the cup (length, depth, rim ratio, no interior peak, bottom
   position, round-not-V), the handle (depth, position in the cup, length) and the breakout
   (recency, not failed).
4. **Stage.** Each candidate gets exactly one target stage, and it is accepted only if every rule that
   stage requires passes:

   | Stage | Meaning |
   |---|---|
   | `BREAKOUT` | First close above the pivot is within the last `breakout.max_age_days` bars and no close since is below the handle low. Flags: `volume_confirmed`, `extended`. |
   | `NEAR_PIVOT` | Cup and handle hold, the handle is at least `handle.min_days` long, and the close is within `breakout.near_pivot_pct` below the pivot. |
   | `HANDLE_FORMING` | Cup and handle hold, but the handle is still short or the price is further below the pivot. |
   | `RIGHT_RIM` | The cup holds; c is unconfirmed (flag `rim_unconfirmed`) or the pullback is still under `handle.min_depth_pct`. |
   | `CUP_FORMING` | Left rim and bottom are in place, the price has recovered part of the depth, and no high since the bottom has reached the rim zone. |

   Older breakouts, failed breakouts, and handles that are too deep or too long are rejected. They
   still show in `--explain` and among the near misses.
5. **One result per ticker.** The most advanced stage wins, then the highest score, then the most
   recent c (or a, when forming).
6. **Score** 0-100 = sum of `scoring.weights` x components in 0..1 (round bottom, symmetry, depth,
   handle, volume dry-up, trend, relative strength).
7. **Levels.** pivot = high[c] (high[a] when forming). stop = handle low (without a handle: the lowest
   low of the last `levels.stop_lookback_days` bars). target = pivot + depth (`absolute`) or
   pivot x (1 + depth / low[b]) (`percent`).

### Flags (informational, never reject)

| Flag | When |
|---|---|
| `volume_confirmed` | Breakout-bar volume >= `breakout.volume_ratio_min` x its average. |
| `extended` | close[T] is more than `breakout.extended_pct` above the pivot. |
| `rim_unconfirmed` | The right rim is inside the last k bars, so it is not a confirmed swing high yet. |
| `left_rim_overhead +X%` | `left_rim_overhead_pct = high[a] / pivot - 1` is at least `flags.left_rim_overhead_min_pct`. The right rim is lower than the left, so a breakout above the pivot still meets resistance at high[a]. The chart draws a thin line there. The percentage is also a summary-table column for every cup (negative when the right rim is higher). |

## Round-not-V calibration

The rule measures the share of closes in a..c (a..T while forming) at or below
`low[b] + cup.u_bottom_zone x depth`, and requires at least `cup.u_min_share_in_zone`.
`python -m scanner.calibrate` prints that share for each zone in `calibration.zones` on:

- synthetic straight-line V, full-cosine U and half-sine U cups, each over `calibration.seeds` noisy seeds
  (mean [min-max]);
- the reference real cups in `calibration/reference_cups.csv`: the EID Parry cup, plus the seven
  results of the 2026-10-26 Nasdaq-100 scan.

```
cup                            zone 0.10   zone 0.15   zone 0.20   zone 0.25   zone 0.30   zone 0.35   zone 0.40
straight-line V (reject)   0.092 [.08-.11] 0.146 [.13-.16] 0.193 [.18-.21] 0.246 [.23-.26] 0.295 [.28-.32] 0.346 [.33-.36] 0.398 [.38-.41]
full-cosine U              0.186 [.17-.20] 0.240 [.22-.25] 0.283 [.27-.29] 0.324 [.31-.34] 0.360 [.35-.38] 0.395 [.38-.40] 0.429 [.42-.44]
half-sine U                0.260 [.23-.28] 0.332 [.31-.34] 0.392 [.37-.40] 0.447 [.44-.46] 0.493 [.48-.51] 0.541 [.53-.56] 0.581 [.57-.59]
EIDPARRY.NS                    0.006       0.076       0.174       0.259       0.372       0.461       0.555
COST                           0.082       0.136       0.264       0.345       0.455       0.682       0.773
LIN                            0.077       0.128       0.205       0.282       0.385       0.513       0.654
WMT                            0.100       0.182       0.291       0.345       0.482       0.582       0.682
PAYX                           0.158       0.263       0.289       0.368       0.395       0.421       0.421
ADSK                           0.025       0.125       0.275       0.325       0.450       0.475       0.500
CTAS                           0.032       0.048       0.095       0.206       0.270       0.429       0.540
TXN (forming, a..T)            0.034       0.124       0.191       0.258       0.326       0.404       0.596
highest V share                0.106       0.159       0.212       0.258       0.318       0.358       0.411
lowest must-pass share         0.006       0.048       0.095       0.206       0.270       0.384       0.417
gap                           -0.100      -0.111      -0.117      -0.052      -0.048      +0.026      +0.007
```

**Choice: zone 0.35, threshold 0.37, `scoring.u_shape_range` [0.36, 0.52].**

- **Small zones fail on real data.** On noise-free synthetic cups they separate V from U best.
  Real bottoms, though, are set by a single spike low, so a thin zone above that low holds almost
  no closes: EID Parry scores 0.076 and CTAS 0.048 at zone 0.15, below every V seed.
- **Only zones of about 0.33 to 0.40 separate** every V seed from every U seed and every reference
  cup. Zone 0.35 has the widest gap (0.026). The threshold sits mid-gap at 0.37, about 2 bars of a
  150-bar cup on either side.
- **Score range.** The highest V seed scores 0 and the lowest half-sine seed scores 1.
- **A robust anchor doesn't help.** Anchoring the zone at the 10th-percentile close instead of the
  spike low gave gaps of 0.012 or less. With noise, a full-cosine U and a straight V simply spend
  similar time near the bottom.
- **Known limitation.** A forming V measured on a..T stops part-way up its right side. One that has
  recovered 55% scores 0.42-0.46 and still passes as CUP_FORMING. Measuring only the left side
  (a..b) does not separate them either: V reaches 0.385 there, while TXN scores 0.377.

## Data and caching

- **Prices.** `yfinance` daily bars, `auto_adjust=True`, explicit start/end, downloaded in batches with
  pauses. Rate limits and empty results are retried with exponential backoff; after that the ticker
  is skipped and the reason recorded.
- **Cache.** One parquet file per ticker plus a small `.meta.json` (requested window and download
  time) in `data/cache/`. A cache entry is reused while it is younger than `cache_max_age_hours` and
  covers the requested window. Otherwise the window is downloaded again and merged in.
- **Unfinished session.** A bar dated today (New York) is dropped before 16:15 New York time. The same
  rule applies to non-US tickers such as `.NS`.
- **Adjusted prices and `--as-of`.** Adjustments for later dividends or splits scale all earlier
  bars by the same factor, so the ratios the detector uses do not change.

## Tests

- **Coverage.** `pytest` covers pivots, the detector, no look-ahead (`--as-of` on full data equals data
  physically truncated), universe parsing, the `--universe` flag, and an offline end-to-end scan.
- **Seeds.** Every synthetic detector test runs over 20 fixed seeds (`scanner/synthetic.py`). The
  scenario tests use the half-sine cup and check a, c and h within 5 bars. b is checked by price
  (low[b] within 1% of the cup's true minimum price), because a round bottom has no single correct
  bar.
- **Shapes.** A straight-line V must be rejected by the round-not-V rule, and a full-cosine U must
  be accepted, on all 20 seeds.
- **No side effects.** Tests never touch the network (Yahoo and Wikipedia are blocked by a fixture)
  and never write to `output/` (a session check fails the run if anything there changes).

## Configuration reference

`config.yaml` holds every threshold. Keys ending in `_pct` are percents (30 means 30%). A
`--config` file is deep-merged over it; `config.relaxed.yaml` holds only its overrides
(`cup.max_days` 400, `handle.max_days` 90, `handle.max_depth_pct` 22,
`handle.min_position_in_cup` 0.3).

### universe
| Key | Default | Meaning |
|---|---|---|
| `active` | sp500 | Universe block to scan; `--universe` overrides it. |
| `user_agent` | descriptive string | HTTP User-Agent for Wikipedia. |
| `request_timeout_sec` | 30 | HTTP timeout. |
| `<name>.url` | Wikipedia list page | Page with the constituents table. |
| `<name>.fallback_urls` | `[]` (nasdaq100: the Nasdaq-100 article) | Pages tried in order if `url` has no usable table. |
| `<name>.ticker_column` | Symbol / Ticker | Column holding the symbols; the table chosen has this column and a row count closest to the expected range. |
| `<name>.company_column` | Security / Company | Company name column. |
| `<name>.sector_column` | GICS Sector / ICB Industry | Sector column, shown in the report. |
| `<name>.saved_file` | `data/universe_<name>.csv` | Saved list: the fallback, and the source of names and sectors. |
| `<name>.min_count`, `<name>.max_count` | 495..510 / 95..105 | Allowed ticker count; outside this range the run fails. |
| `<name>.benchmark` | SPY / QQQ | Downloaded for market context only; not scanned. |

### data
| Key | Default | Meaning |
|---|---|---|
| `history_years` | 3 | Window length, ending at `--as-of` or today. |
| `batch_size` | 20 | Tickers per yfinance call. |
| `pause_between_batches_sec` | 2 | Pause between batches. |
| `max_retries` | 4 | Retries after a rate limit or an empty result. |
| `backoff_base_sec` | 10 | Wait before retry n is `backoff_base_sec x 2^n`. |
| `request_timeout_sec` | 30 | yfinance timeout. |
| `cache_dir` | `data/cache` | Parquet cache, shared by all universes. |
| `cache_max_age_hours` | 12 | Cache entries older than this are downloaded again. |
| `min_bars` | 250 | Tickers with fewer bars are skipped. |
| `session_timezone` | America/New_York | Time zone for "today" and the unfinished-session rule. |
| `session_close_time` | "16:15" | Before this time, a bar dated today is dropped. |

### pivots
| Key | Default | Meaning |
|---|---|---|
| `swing_window` | 5 | k: bars on each side of a swing high or low. |

### prior_uptrend
| Key | Default | Meaning |
|---|---|---|
| `lookback_days` | 189 | Bars before a used to find the starting low (about 9 months). |
| `min_rise_pct` | 30 | Minimum rise: high[a] / lowest low in the lookback - 1. |

### cup
| Key | Default | Meaning |
|---|---|---|
| `min_days`, `max_days` | 30, 325 | Allowed c - a (T - a when forming). |
| `min_depth_pct`, `max_depth_pct` | 12, 35 | Allowed depth: (high[a] - low[b]) / high[a]. |
| `rim_min_ratio`, `rim_max_ratio` | 0.90, 1.05 | Allowed high[c] / high[a]. A forming cup's close, and every high since its bottom, must stay below `rim_min_ratio` x high[a]. |
| `left_rim_dominance_days` | 60 | high[a] must be the highest high of this many prior bars. |
| `bottom_position_min`, `bottom_position_max` | 0.15, 0.85 | Allowed (b - a) / (c - a). |
| `u_bottom_zone` | 0.35 | Bottom zone: low[b] + this x depth (calibrated, see above). |
| `u_min_share_in_zone` | 0.37 | Round-not-V: minimum share of closes in a..c inside the bottom zone (calibrated). |
| `forming_min_recovery` | 0.5 | Forming cup: (close[T] - low[b]) / depth must reach this. |

### handle
| Key | Default | Meaning |
|---|---|---|
| `min_days` | 5 | Minimum handle length for NEAR_PIVOT and BREAKOUT. |
| `max_days` | 30 | Maximum handle length. |
| `max_days_vs_cup` | 0.5 | Maximum handle length as a fraction of c - a. |
| `min_depth_pct`, `max_depth_pct` | 2, 12 | Allowed (pivot - low[h]) / pivot. Below the minimum, the candidate is still at its right rim. |
| `min_position_in_cup` | 0.5 | low[h] must be at least low[b] + this x depth (upper half of the cup). |

### breakout
| Key | Default | Meaning |
|---|---|---|
| `near_pivot_pct` | 5 | NEAR_PIVOT if close[T] is within this % below the pivot. |
| `volume_avg_days` | 50 | Average-volume window for the breakout volume ratio and the handle dry-up. |
| `volume_ratio_min` | 1.4 | `volume_confirmed` if breakout-bar volume / average >= this. |
| `max_age_days` | 10 | The breakout bar must be among the last this-many bars. |
| `extended_pct` | 5 | `extended` if close[T] > pivot x (1 + this %). |

### levels, flags and targets
| Key | Default | Meaning |
|---|---|---|
| `levels.stop_lookback_days` | 10 | Stop without a handle: lowest low of this many recent bars. |
| `flags.left_rim_overhead_min_pct` | 3 | Flag `left_rim_overhead` (and draw the left-rim line) when high[a] is at least this % above the pivot. |
| `targets.method` | absolute | `absolute`: pivot + depth. `percent`: pivot x (1 + depth / low[b]), projecting the cup's % rise. |

### scoring
| Key | Default | Meaning |
|---|---|---|
| `weights` | u_shape 25, symmetry 10, depth 10, handle 20, volume 15, trend 10, rel_strength 10 | Points per component (sum 100). |
| `u_shape_range` | [0.36, 0.52] | Share of closes in the bottom zone mapped linearly to 0..1: the highest V seed scores 0, the lowest half-sine seed scores 1. |
| `symmetry_tolerance` | 0.10 | symmetry = 1 - min(1, abs(ln(high[c] / high[a])) / this). |
| `depth_sweet_spot_pct` | [15, 30] | Depth scores 1 inside this range and falls linearly to 0 at the cup depth limits. |
| `handle_sweet_spot_pct` | [3, 10] | Handle scores 1 if its depth is in this range and its low is in the upper zone, 0.5 if only one holds. |
| `handle_upper_zone` | 0.3333 | "Upper third": low[h] >= low[b] + (1 - this) x depth. |
| `volume_dryup_range` | [0.8, 1.2] | r = handle volume / volume before c; 1 at or below the first value, 0 at or above the second. |
| `sma_fast_days`, `sma_slow_days` | 50, 200 | Trend: +0.5 if close > SMA fast, +0.5 if SMA fast > SMA slow. Also used for the benchmark context. |
| `rs_lookback_days` | 126 | Return window for relative strength. |
| `rs_min_universe` | 20 | With fewer tickers in the run, relative strength is `no_data_value`. |
| `no_data_value` | 0.5 | Component value when it cannot be measured yet (no handle, forming cup, small universe). |

### calibration
| Key | Default | Meaning |
|---|---|---|
| `zones` | [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40] | Bottom-zone sizes measured by `python -m scanner.calibrate`. |
| `seeds` | 20 | Noisy synthetic cups per shape. |
| `reference_file` | `calibration/reference_cups.csv` | Real cups that must pass (ticker, a, c, as-of). |

### report
| Key | Default | Meaning |
|---|---|---|
| `output_dir` | output | Reports go to `output_dir/<universe>/<last session date>/`. |
| `chart_padding_days` | 60 | Bars shown before a. |
| `near_misses` | 10 | Maximum near misses listed (one per ticker, exactly one failed rule). |
| `max_charts` | 60 | Charts rendered and embedded for the best results (stage priority, then score). |
| `max_html_mb` | 15 | Log a warning if report.html grows beyond this. |
| `console_top` | 10 | Results printed to the console. |
| `explain_top` | 20 | Candidates printed by `--explain`. |
| `run_timezone` | Asia/Jerusalem | Time zone for the run time in the report header. |
| `chart_width_px`, `chart_height_px`, `chart_dpi` | 1600, 900, 100 | Chart size. |
| `chart_font_size` | 15 | Base chart font size. |

## Notes

- **Wikipedia URLs.** The Nasdaq-100 components table lives on `List_of_NASDAQ-100_companies` (it left
  the `Nasdaq-100` article in 2026); the old article is kept as a fallback.
- **Extra forming-cup rule.** `forming_no_right_rim` rejects a "forming" cup whose price has already
  reached `rim_min_ratio` x high[a] since the bottom. Such a base already has a right rim and is
  judged by its handle instead.
- **Not investment advice.**
