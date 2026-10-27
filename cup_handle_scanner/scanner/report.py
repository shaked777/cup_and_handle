"""report.html (self-contained), report.md, results.csv, results.json and the console summary."""
from __future__ import annotations

import base64
import csv
import json
import logging
import math
from collections import Counter
from pathlib import Path

from jinja2 import Environment

from .detector import STAGES, format_flags

log = logging.getLogger(__name__)

STAGE_NOTES = {
    "BREAKOUT": "Closed above the pivot within the last few bars and has not failed.",
    "NEAR_PIVOT": "Cup and handle complete; price is just under the pivot.",
    "HANDLE_FORMING": "Cup complete; the handle is still short or price is well under the pivot.",
    "RIGHT_RIM": "Cup complete up to the right rim; no handle pullback yet.",
    "CUP_FORMING": "Left side and bottom in place; price is climbing the right side.",
}

SUMMARY_COLUMNS = [
    ("ticker", "Ticker"), ("company", "Company"), ("sector", "Sector"), ("stage", "Stage"),
    ("score", "Score"), ("close", "Close"), ("pivot", "Pivot"), ("pct_to_pivot", "% to pivot"),
    ("cup_depth_pct", "Cup depth %"), ("cup_weeks", "Cup wks"), ("handle_depth_pct", "Handle %"),
    ("handle_days", "Handle days"), ("stop", "Stop"), ("target", "Target"),
    ("volume_ratio", "Vol ratio"), ("overhead_pct", "Left rim overhead %"), ("flags", "Flags"),
]
ONE_DECIMAL = ("score", "cup_depth_pct", "cup_weeks", "handle_depth_pct", "pct_to_pivot", "overhead_pct")
NO_DECIMAL = ("handle_days",)


def _num(v, d=2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:,.{d}f}"


def _decimals(key: str) -> int:
    return 1 if key in ONE_DECIMAL else (0 if key in NO_DECIMAL else 2)


def summary_row(r) -> dict:
    c, b = r.best, r.bars
    m, lv = c.m, c.levels
    overhead = m.get("left_rim_overhead_pct")
    return {
        "ticker": r.ticker, "company": r.company, "sector": r.sector, "stage": c.stage,
        "score": round(c.score, 1), "close": round(float(b.close[-1]), 2), "pivot": round(lv["pivot"], 2),
        "pct_to_pivot": round(lv["distance_to_pivot_pct"], 2),
        "cup_depth_pct": round(m["depth_pct"], 1), "cup_weeks": round(m["cup_days"] / 5, 1),
        "handle_depth_pct": round(m["handle_depth_pct"], 1) if c.has_handle else None,
        "handle_days": m["handle_len"] if c.has_handle else None,
        "stop": round(lv["stop"], 2), "target": round(lv["target"], 2),
        "volume_ratio": round(m["volume_ratio"], 2) if m.get("volume_ratio") is not None else None,
        "overhead_pct": None if overhead is None else round(overhead, 1),
        "flags": format_flags(c),
        "a": b.date(c.a), "b": b.date(c.b), "c": b.date(c.c), "h": b.date(c.h) if c.has_handle else "",
        "e": b.date(c.e), "last_bar": b.date(b.T),
    }


def _rules(c) -> list[dict]:
    return [{"name": x.name, "group": x.group, "status": x.status(), "required": x.required,
             "measured": x.shown, "value": x.value, "threshold": x.threshold} for x in c.rules]


def _near_row(r) -> dict:
    c, b = r.near_miss, r.bars
    f = c.failed[0]
    return {"ticker": r.ticker, "company": r.company, "sector": r.sector, "would_be": c.target_stage,
            "score": round(c.score, 1), "rule": f.name, "measured": f.shown, "threshold": f.threshold,
            "a": b.date(c.a), "c": b.date(c.c), "last_bar": b.date(b.T)}


def sector_counts(found) -> list[dict]:
    """Results per sector and stage, most results first."""
    by = Counter((r.sector or "(none)", r.best.stage) for r in found)
    sectors = Counter(r.sector or "(none)" for r in found)
    return [{"sector": s, "total": n, **{st: by[(s, st)] for st in STAGES}}
            for s, n in sorted(sectors.items(), key=lambda kv: (-kv[1], kv[0]))]


def _key_config(cfg: dict) -> list[tuple[str, str]]:
    cu, ha, bo, pu = cfg["cup"], cfg["handle"], cfg["breakout"], cfg["prior_uptrend"]
    return [
        ("Prior uptrend", f">= {pu['min_rise_pct']}% within {pu['lookback_days']} bars"),
        ("Cup length", f"{cu['min_days']}..{cu['max_days']} bars"),
        ("Cup depth", f"{cu['min_depth_pct']}..{cu['max_depth_pct']}%"),
        ("Rim ratio c/a", f"{cu['rim_min_ratio']}..{cu['rim_max_ratio']}"),
        ("Round bottom", f">= {cu['u_min_share_in_zone']} of closes in bottom {cu['u_bottom_zone']:.0%}"),
        ("Handle length", f"{ha['min_days']}..{ha['max_days']} bars, <= {ha['max_days_vs_cup']} x cup"),
        ("Handle depth", f"{ha['min_depth_pct']}..{ha['max_depth_pct']}%, low above "
                         f"{ha['min_position_in_cup']:.0%} of cup"),
        ("Near pivot", f"within {bo['near_pivot_pct']}%"),
        ("Breakout", f"<= {bo['max_age_days']} bars old, volume >= {bo['volume_ratio_min']}x "
                     f"{bo['volume_avg_days']}-day avg"),
        ("Left rim overhead flag", f">= {cfg['flags']['left_rim_overhead_min_pct']}% above pivot"),
        ("Target method", cfg["targets"]["method"]),
        ("History", f"{cfg['data']['history_years']} years daily"),
    ]


def _context(found, near, ctx, cfg, out_dir: Path, embed: bool) -> dict:
    stages = []
    for s in STAGES:
        items = []
        for r in (x for x in found if x.best.stage == s):
            img = None
            if r.chart:
                p = out_dir / r.chart
                img = ("data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()) if embed else r.chart
            items.append({"row": summary_row(r), "rules": _rules(r.best), "img": img,
                          "components": {k: round(v, 2) for k, v in r.best.components.items()},
                          "weights": cfg["scoring"]["weights"]})
        stages.append({"name": s, "note": STAGE_NOTES[s], "entries": items})
    return {
        "ctx": ctx, "cfg": cfg, "stages": stages, "stage_names": STAGES,
        "rows": [summary_row(r) for r in found], "cols": SUMMARY_COLUMNS,
        "near": [_near_row(r) for r in near],
        "skipped": sorted(ctx["skipped"].items()),
        "counts": {s: sum(1 for r in found if r.best.stage == s) for s in STAGES},
        "sectors": sector_counts(found),
        "charted": sum(1 for r in found if r.chart), "max_charts": cfg["report"]["max_charts"],
        "market": ctx["market"], "key_config": _key_config(cfg),
        "run_time": ctx["run_time"].strftime("%Y-%m-%d %H:%M %Z"),
        "decimals": _decimals,
    }


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cup and handle scan {{ ctx.universe }} {{ ctx.last_session }}</title>
<style>
:root{--bg:#fafafa;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e3e6;--pass:#1b7f3b;--fail:#c62828;--accent:#1565c0}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--fg:#ececf0;--muted:#9a9aa2;--card:#1c1c1f;--line:#2e2e33;--pass:#5cc97b;--fail:#ff6b6b;--accent:#6ea8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.2rem;margin:1.6em 0 .4em;border-bottom:1px solid var(--line);padding-bottom:.2em}
h3{font-size:1.05rem;margin:.2em 0}.muted{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:.6em 0}.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:.85rem;text-decoration:none;color:var(--fg)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:.85rem}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--card)}td.n{text-align:right;font-variant-numeric:tabular-nums}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0}
.card img{width:100%;height:auto;border-radius:6px;display:block;margin:8px 0}
.PASS{color:var(--pass);font-weight:600}.FAIL{color:var(--fail);font-weight:600}.na{color:var(--muted)}
.rules td{white-space:normal}.opt{color:var(--muted)}
.levels{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:.9rem}
details summary{cursor:pointer;color:var(--accent)}
.sectors{max-width:100%;margin:.4em 0}.sectors td.z{color:var(--muted)}
</style></head><body><main>
<h1>Cup-and-handle scan: {{ ctx.universe }}</h1>
<div class="muted">Last session {{ ctx.last_session }} (New York){% if ctx.as_of %} · as-of {{ ctx.as_of }}{% endif %} · run {{ run_time }} · config {{ ctx.config_name }}</div>
<div class="chips">
<span class="chip">Scanned {{ ctx.scanned }} of {{ ctx.requested }}</span>
<span class="chip">Skipped {{ skipped|length }}</span>
{% for s in stages %}<a class="chip" href="#{{ s.name }}">{{ s.name }}: {{ counts[s.name] }}</a>{% endfor %}
</div>
{% if market %}<p><b>{{ market.ticker }}</b> {{ num(market.close) }} ·
SMA{{ market.fast_days }} {{ num(market.sma_fast) }} ({{ "above" if market.above_fast else "below" }}) ·
SMA{{ market.slow_days }} {{ num(market.sma_slow) }} ({{ "above" if market.above_slow else "below" }})</p>{% endif %}
{% if sectors %}<details open><summary>Results by sector and stage</summary><div class="scroll sectors"><table>
<tr><th>Sector</th>{% for st in stage_names %}<th>{{ st }}</th>{% endfor %}<th>Total</th></tr>
{% for s in sectors %}<tr><td>{{ s.sector }}</td>{% for st in stage_names %}<td class="n{{ ' z' if not s[st] }}">{{ s[st] }}</td>{% endfor %}<td class="n"><b>{{ s.total }}</b></td></tr>{% endfor %}
</table></div></details>{% endif %}
<details><summary>Key config values</summary><div class="scroll"><table>
{% for k, v in key_config %}<tr><td>{{ k }}</td><td>{{ v }}</td></tr>{% endfor %}</table></div></details>

<h2 id="summary">Summary</h2>
{% if rows %}{% if charted < rows|length %}<p class="muted">Charts for the top {{ charted }} results (report.max_charts {{ max_charts }}); the rest are listed with their measurements.</p>{% endif %}
<div class="scroll"><table><tr>{% for k, label in cols %}<th>{{ label }}</th>{% endfor %}</tr>
{% for r in rows %}<tr>{% for k, label in cols %}{% set v = r[k] %}
<td class="{{ 'n' if v is number else '' }}">{% if k == 'ticker' %}<a href="#t-{{ v }}">{{ v }}</a>{% elif v is number %}{{ num(v, decimals(k)) }}{% elif v is none %}–{% else %}{{ v }}{% endif %}</td>{% endfor %}</tr>
{% endfor %}</table></div>{% else %}<p class="muted">No ticker passed every rule.</p>{% endif %}

{% for s in stages %}<h2 id="{{ s.name }}">{{ s.name }} ({{ s.entries|length }})</h2>
<p class="muted">{{ s.note }}</p>
{% for it in s.entries %}{% set r = it.row %}<div class="card" id="t-{{ r.ticker }}">
<h3>{{ r.ticker }} <span class="muted">{{ r.company }}{% if r.sector %} · {{ r.sector }}{% endif %}</span> · score {{ num(r.score, 1) }}{% if r.flags %} · {{ r.flags }}{% endif %}</h3>
<div class="levels"><span>Close {{ num(r.close) }}</span><span>Pivot {{ num(r.pivot) }} ({{ "%+.1f"|format(r.pct_to_pivot) }}%)</span>
<span>Stop {{ num(r.stop) }}</span><span>Target {{ num(r.target) }}</span>
<span>a {{ r.a }} · b {{ r.b }}{% if r.c %} · c {{ r.c }}{% endif %}{% if r.h %} · h {{ r.h }}{% endif %}{% if r.e %} · e {{ r.e }}{% endif %}</span></div>
{% if it.img %}<img src="{{ it.img }}" alt="{{ r.ticker }} chart" loading="lazy">{% else %}<p class="muted">No chart (beyond the top {{ max_charts }}).</p>{% endif %}
<details><summary>Rules and score</summary><div class="scroll"><table class="rules">
<tr><th>Status</th><th>Rule</th><th>Measured</th><th>Threshold</th></tr>
{% for x in it.rules %}<tr class="{{ '' if x.required else 'opt' }}"><td class="{{ x.status if x.status != 'n/a' else 'na' }}">{{ x.status if x.required else x.status|lower }}</td><td>{{ x.name }}</td><td>{{ x.measured }}</td><td>{{ x.threshold }}</td></tr>{% endfor %}
</table></div>
<p class="muted">Score components (value x weight): {% for k, v in it.components.items() %}{{ k }} {{ v }}x{{ it.weights[k] }}{{ ", " if not loop.last }}{% endfor %}. Lower-case status = not required at this stage.</p></details>
</div>{% else %}<p class="muted">None.</p>{% endfor %}{% endfor %}

<h2 id="near">Near misses</h2>
<p class="muted">Best candidate per ticker that failed exactly one rule (tickers without a result).</p>
{% if near %}<div class="scroll"><table><tr><th>Ticker</th><th>Company</th><th>Sector</th><th>Would be</th><th>Score</th><th>Failed rule</th><th>Measured</th><th>Threshold</th><th>a</th><th>c</th></tr>
{% for n in near %}<tr><td>{{ n.ticker }}</td><td>{{ n.company }}</td><td>{{ n.sector }}</td><td>{{ n.would_be }}</td><td class="n">{{ num(n.score, 1) }}</td><td>{{ n.rule }}</td><td>{{ n.measured }}</td><td>{{ n.threshold }}</td><td>{{ n.a }}</td><td>{{ n.c }}</td></tr>{% endfor %}
</table></div>{% else %}<p class="muted">None.</p>{% endif %}

<h2 id="skipped">Skipped tickers</h2>
{% if skipped %}<ul>{% for t, why in skipped %}<li><b>{{ t }}</b>: {{ why }}</li>{% endfor %}</ul>{% else %}<p class="muted">None.</p>{% endif %}
<p class="muted">Data: Yahoo Finance daily bars via yfinance (adjusted). Universe: Wikipedia. Not investment advice.</p>
</main></body></html>
"""

MD = r"""# Cup-and-handle scan: {{ ctx.universe }}, {{ ctx.last_session }}

Last session **{{ ctx.last_session }}** (New York){% if ctx.as_of %}, as-of {{ ctx.as_of }}{% endif %} · run {{ run_time }} · config `{{ ctx.config_name }}`

Scanned {{ ctx.scanned }} of {{ ctx.requested }} · skipped {{ skipped|length }} · {% for s in stages %}{{ s.name }} {{ counts[s.name] }}{{ " · " if not loop.last }}{% endfor %}
{% if market %}
**{{ market.ticker }}** {{ num(market.close) }} · SMA{{ market.fast_days }} {{ num(market.sma_fast) }} ({{ "above" if market.above_fast else "below" }}) · SMA{{ market.slow_days }} {{ num(market.sma_slow) }} ({{ "above" if market.above_slow else "below" }})
{% endif %}{% if sectors %}
<details open><summary>Results by sector and stage</summary>

| Sector | {% for st in stage_names %}{{ st }} | {% endfor %}Total |
|---|{% for st in stage_names %}---|{% endfor %}---|
{% for s in sectors %}| {{ s.sector }} | {% for st in stage_names %}{{ s[st] }} | {% endfor %}**{{ s.total }}** |
{% endfor %}
</details>
{% endif %}
<details><summary>Key config values</summary>

| Setting | Value |
|---|---|
{% for k, v in key_config %}| {{ k }} | {{ v }} |
{% endfor %}
</details>

## Summary
{% if rows %}{% if charted < rows|length %}
Charts for the top {{ charted }} results (report.max_charts {{ max_charts }}); the rest are listed with their measurements.
{% endif %}
| {% for k, label in cols %}{{ label }} | {% endfor %}
|{% for k, label in cols %}---|{% endfor %}
{% for r in rows %}| {% for k, label in cols %}{% set v = r[k] %}{% if v is number %}{{ num(v, decimals(k)) }}{% elif v is none %}–{% else %}{{ v }}{% endif %} | {% endfor %}
{% endfor %}{% else %}
No ticker passed every rule.
{% endif %}
{% for s in stages %}
## {{ s.name }} ({{ s.entries|length }})

_{{ s.note }}_
{% for it in s.entries %}{% set r = it.row %}
### {{ r.ticker }} · {{ r.company }}{% if r.sector %} · {{ r.sector }}{% endif %} · score {{ num(r.score, 1) }}{% if r.flags %} · {{ r.flags }}{% endif %}

Close {{ num(r.close) }} · pivot {{ num(r.pivot) }} ({{ "%+.1f"|format(r.pct_to_pivot) }}%) · stop {{ num(r.stop) }} · target {{ num(r.target) }} · a {{ r.a }} · b {{ r.b }}{% if r.c %} · c {{ r.c }}{% endif %}{% if r.h %} · h {{ r.h }}{% endif %}{% if r.e %} · e {{ r.e }}{% endif %}
{% if it.img %}
![{{ r.ticker }}]({{ it.img }})
{% else %}
_No chart (beyond the top {{ max_charts }})._
{% endif %}
<details><summary>Rules</summary>

| Status | Rule | Measured | Threshold |
|---|---|---|---|
{% for x in it.rules %}| {{ x.status if x.required else x.status|lower }} | {{ x.name }} | {{ x.measured }} | {{ x.threshold }} |
{% endfor %}
Score components: {% for k, v in it.components.items() %}{{ k }} {{ v }}x{{ it.weights[k] }}{{ ", " if not loop.last }}{% endfor %}
</details>
{% else %}
None.
{% endfor %}{% endfor %}
## Near misses

Best candidate per ticker that failed exactly one rule (tickers without a result).
{% if near %}
| Ticker | Company | Sector | Would be | Score | Failed rule | Measured | Threshold | a | c |
|---|---|---|---|---|---|---|---|---|---|
{% for n in near %}| {{ n.ticker }} | {{ n.company }} | {{ n.sector }} | {{ n.would_be }} | {{ num(n.score, 1) }} | {{ n.rule }} | {{ n.measured }} | {{ n.threshold }} | {{ n.a }} | {{ n.c }} |
{% endfor %}{% else %}
None.
{% endif %}
## Skipped tickers
{% if skipped %}
{% for t, why in skipped %}- **{{ t }}**: {{ why }}
{% endfor %}{% else %}
None.
{% endif %}
"""


def _env(autoescape: bool) -> Environment:
    env = Environment(autoescape=autoescape)
    env.globals["num"] = _num
    return env


def write_all(found, near, ctx, cfg, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "report.html"
    html_path.write_text(
        _env(True).from_string(HTML).render(**_context(found, near, ctx, cfg, out_dir, embed=True)),
        encoding="utf-8")
    size_mb = html_path.stat().st_size / 1e6
    if size_mb > cfg["report"]["max_html_mb"]:
        log.warning("report.html is %.1f MB (> report.max_html_mb %s); lower report.max_charts",
                    size_mb, cfg["report"]["max_html_mb"])
    (out_dir / "report.md").write_text(
        _env(False).from_string(MD).render(**_context(found, near, ctx, cfg, out_dir, embed=False)),
        encoding="utf-8")

    rows = [summary_row(r) for r in found]
    fields = [k for k, _ in SUMMARY_COLUMNS] + ["a", "b", "c", "h", "e", "last_bar"]
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    payload = {
        "universe": ctx["universe"], "last_session": str(ctx["last_session"]),
        "run_time": ctx["run_time"].isoformat(),
        "as_of": str(ctx["as_of"]) if ctx["as_of"] else None, "config": ctx["config_name"],
        "scanned": ctx["scanned"], "requested": ctx["requested"], "market": ctx["market"],
        "sector_counts": sector_counts(found),
        "results": [dict(summary_row(r), flag_names=list(r.best.flags), chart=r.chart,
                         rules=_rules(r.best), components=r.best.components) for r in found],
        "near_misses": [_near_row(r) for r in near],
        "skipped": ctx["skipped"],
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def print_console(found, ctx, cfg, out_dir: Path) -> None:
    counts = {s: sum(1 for r in found if r.best.stage == s) for s in STAGES}
    print(f"\n{ctx['universe']}: last session {ctx['last_session']}  scanned {ctx['scanned']}/{ctx['requested']}  "
          f"skipped {len(ctx['skipped'])}")
    print("  ".join(f"{s} {n}" for s, n in counts.items()))
    top = sorted(found, key=lambda r: -r.best.score)[: cfg["report"]["console_top"]]
    if top:
        print(f"\nTop {len(top)} by score:")
        print(f"  {'ticker':<8}{'stage':<16}{'score':>6}{'close':>10}{'pivot':>10}{'to pivot':>10}  flags")
        for r in top:
            lv = r.best.levels
            print(f"  {r.ticker:<8}{r.best.stage:<16}{r.best.score:>6.1f}{r.bars.close[-1]:>10.2f}"
                  f"{lv['pivot']:>10.2f}{lv['distance_to_pivot_pct']:>+9.1f}%  {format_flags(r.best)}")
    print(f"\nReport: {out_dir / 'report.html'}")
