# Nasdaq-100 cup-and-handle scanner

Scans the current Nasdaq-100 constituents on daily bars, finds cup-and-handle patterns (a fresh
breakout, or a pattern still forming) and writes a ranked report with annotated charts.

Everything is free: Yahoo Finance daily bars through `yfinance`, the constituents table from
Wikipedia, open-source Python libraries. No API keys, no paid services, no AI calls.

Each run scans once, on the latest data (or an `--as-of` date). `scanner.cli.main()` is the single
non-interactive entry point, so a scheduler can call it later.

## Setup

Python 3.10 or newer.

```bash
cd cup_handle_scanner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

## Usage

```bash
.venv/bin/python -m scanner.cli [--config PATH] [--tickers A,B] [--refresh] [--as-of YYYY-MM-DD] [--explain TICKER]
```

| Option | What it does |
|---|---|
| *(none)* | Scan the whole Nasdaq-100 on the latest completed session and write the report. |
| `--config PATH` | YAML file deep-merged over `config.yaml`. It only needs the keys it changes (see `config.relaxed.yaml`). |
| `--tickers A,B` | Scan only these symbols. They may be outside the index (e.g. `EIDPARRY.NS`). |
| `--refresh` | Ignore the cache and download again. |
| `--as-of YYYY-MM-DD` | End the data window at that date. Nothing after it is downloaded or used. |
| `--explain TICKER` | Scan only that ticker and print its top candidates with every rule, the measured value and pass/fail. Writes no report files. |

Examples:

```bash
.venv/bin/python -m scanner.cli
.venv/bin/python -m scanner.cli --tickers AAPL,MSFT,NVDA
.venv/bin/python -m scanner.cli --explain EIDPARRY.NS --as-of 2024-05-31 --config config.relaxed.yaml
```

In `--explain` output, a status in capitals (PASS/FAIL) is required for that candidate's stage;
lower case is informational (for example the handle-depth minimum while a cup is still at its
right rim).

## Output

`output/<last session date>/`

| File | Contents |
|---|---|
| `report.html` | Self-contained (charts embedded), mobile-friendly. Header with run time (Asia/Jerusalem), last session (New York), scanned/skipped counts, QQQ against its SMA50/SMA200 and key config values; summary table; one section per stage with chart and full rule list; near misses; skipped tickers. |
| `report.md` | The same report for GitHub, linking `charts/*.png`. |
| `results.csv` | One row per result: the summary-table columns plus the a/b/c/h/e dates. |
| `results.json` | Results with every rule and score component, near misses, skipped tickers and market context. |
| `charts/*.png` | 1600x900 chart per result: candles and volume, a/b/c/h (and breakout e) markers, a quadratic fitted to the cup lows, a dashed pivot line, stop and target lines, the shaded handle and a caption box. |

The console prints counts per stage and the top results.

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
   | `RIGHT_RIM` | The cup holds; c is unconfirmed or the pullback is still under `handle.min_depth_pct`. |
   | `CUP_FORMING` | Left rim and bottom are in place, the price has recovered part of the depth, and no right rim yet. |

   Older breakouts, failed breakouts, and handles that are too deep or too long are rejected. They
   still show in `--explain` and among the near misses.
5. **One result per ticker.** The most advanced stage wins, then the highest score, then the most
   recent c (or a, when forming).
6. **Score** 0-100 = sum of `scoring.weights` x components in 0..1 (round bottom, symmetry, depth,
   handle, volume dry-up, trend, relative strength).
7. **Levels.** pivot = high[c] (high[a] when forming). stop = handle low (without a handle: the lowest
   low of the last `levels.stop_lookback_days` bars). target = pivot + depth (`absolute`) or
   pivot x (1 + depth / low[b]) (`percent`).

## Data and caching

- **Universe.** The Wikipedia constituents table is saved to `data/universe.csv`. If the fetch fails,
  that file is used with a warning. The run fails loudly if the ticker count is outside
  `universe.min_count..max_count`. Tickers are upper-cased, with `.` replaced by `-`.
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

## Configuration reference

`config.yaml` holds every threshold. Keys ending in `_pct` are percents (30 means 30%). A
`--config` file is deep-merged over it; `config.relaxed.yaml` holds only its overrides
(`cup.max_days` 400, `handle.max_days` 90, `handle.max_depth_pct` 22,
`handle.min_position_in_cup` 0.3).

### universe
| Key | Default | Meaning |
|---|---|---|
| `url` | `.../wiki/List_of_NASDAQ-100_companies` | Page with the constituents table (a "Ticker" or "Symbol" column with about 100 rows). |
| `fallback_urls` | `[.../wiki/Nasdaq-100]` | Pages tried in order if `url` has no usable table. |
| `user_agent` | descriptive string | HTTP User-Agent for Wikipedia. |
| `request_timeout_sec` | 30 | HTTP timeout. |
| `saved_file` | `data/universe.csv` | Saved constituents, used as the fallback and for company names. |
| `min_count`, `max_count` | 95, 105 | Allowed ticker count; outside this range the run fails. |
| `benchmark` | QQQ | Downloaded for market context only; not scanned. |

### data
| Key | Default | Meaning |
|---|---|---|
| `history_years` | 3 | Window length, ending at `--as-of` or today. |
| `batch_size` | 20 | Tickers per yfinance call. |
| `pause_between_batches_sec` | 2 | Pause between batches. |
| `max_retries` | 4 | Retries after a rate limit or an empty result. |
| `backoff_base_sec` | 10 | Wait before retry n is `backoff_base_sec x 2^n`. |
| `request_timeout_sec` | 30 | yfinance timeout. |
| `cache_dir` | `data/cache` | Parquet cache. |
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
| `rim_min_ratio`, `rim_max_ratio` | 0.90, 1.05 | Allowed high[c] / high[a]. Forming cups must stay below `rim_min_ratio` x high[a]. |
| `left_rim_dominance_days` | 60 | high[a] must be the highest high of this many prior bars. |
| `bottom_position_min`, `bottom_position_max` | 0.15, 0.85 | Allowed (b - a) / (c - a). |
| `u_bottom_zone` | 0.35 | Bottom zone: low[b] + this x depth. |
| `u_min_share_in_zone` | 0.40 | Round-not-V: minimum share of closes in a..c inside the bottom zone (a straight V gives about 0.35). |
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

### levels and targets
| Key | Default | Meaning |
|---|---|---|
| `levels.stop_lookback_days` | 10 | Stop without a handle: lowest low of this many recent bars. |
| `targets.method` | absolute | `absolute`: pivot + depth. `percent`: pivot x (1 + depth / low[b]), projecting the cup's % rise. |

### scoring
| Key | Default | Meaning |
|---|---|---|
| `weights` | u_shape 25, symmetry 10, depth 10, handle 20, volume 15, trend 10, rel_strength 10 | Points per component (sum 100). |
| `u_shape_range` | [0.35, 0.55] | Share of closes in the bottom zone mapped linearly to 0..1. |
| `symmetry_tolerance` | 0.10 | symmetry = 1 - min(1, abs(ln(high[c] / high[a])) / this). |
| `depth_sweet_spot_pct` | [15, 30] | Depth scores 1 inside this range and falls linearly to 0 at the cup depth limits. |
| `handle_sweet_spot_pct` | [3, 10] | Handle scores 1 if its depth is in this range and its low is in the upper zone, 0.5 if only one holds. |
| `handle_upper_zone` | 0.3333 | "Upper third": low[h] >= low[b] + (1 - this) x depth. |
| `volume_dryup_range` | [0.8, 1.2] | r = handle volume / volume before c; 1 at or below the first value, 0 at or above the second. |
| `sma_fast_days`, `sma_slow_days` | 50, 200 | Trend: +0.5 if close > SMA fast, +0.5 if SMA fast > SMA slow. Also used for QQQ context. |
| `rs_lookback_days` | 126 | Return window for relative strength (percentile rank among scanned tickers). |
| `rs_min_universe` | 20 | With fewer scanned tickers, relative strength is `no_data_value`. |
| `no_data_value` | 0.5 | Component value when it cannot be measured yet (no handle, forming cup, small universe). |

### report
| Key | Default | Meaning |
|---|---|---|
| `output_dir` | output | Reports go to `output_dir/<last session date>/`. |
| `chart_padding_days` | 60 | Bars shown before a. |
| `near_misses` | 10 | Maximum near misses listed (one per ticker, exactly one failed rule). |
| `console_top` | 10 | Results printed to the console. |
| `explain_top` | 20 | Candidates printed by `--explain`. |
| `run_timezone` | Asia/Jerusalem | Time zone for the run time in the report header. |
| `chart_width_px`, `chart_height_px`, `chart_dpi` | 1600, 900, 100 | Chart size. |
| `chart_font_size` | 15 | Base chart font size. |

## Notes

- **Wikipedia URL.** In October 2026 the components table was no longer on the `Nasdaq-100`
  article. It lives on `List_of_NASDAQ-100_companies`, which is now `universe.url`. The old
  article is kept as a fallback.
- **Extra forming-cup rule.** `forming_no_right_rim` rejects a "forming" cup whose price has
  already reached `rim_min_ratio` x high[a] since the bottom. Such a base already has a right rim
  and is judged by its handle instead.
- **Not investment advice.**
