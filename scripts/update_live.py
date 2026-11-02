#!/usr/bin/env python3
"""
Live updater for US Elections Decoded.

1. Fetches each forecaster in data/sources.json and extracts the Democratic
   chance of winning the House and Senate (and DDHQ per-state Senate/governor
   probabilities).
2. Keeps the last good value when a source cannot be read, and marks it stale.
3. Writes data/latest.json, appends data/history.csv, updates the race CSVs.
4. Redraws every chart in assets/ and rewrites the LIVE block of README.md.

Usage:
    python3 scripts/update_live.py            # fetch + rebuild
    python3 scripts/update_live.py --offline  # rebuild from stored data only

The creator of this repository is neutral and does not support any political
party or ideology. This script only reports what public sources publish.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources_engine as SE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

D_BLUE = "#1d4ed8"
R_RED = "#b91c1c"
TOSS = "#9ca3af"
INK = "#111827"
MUTED = "#6b7280"
GRID = "#e5e7eb"
SURFACE = "#ffffff"

RATING_ORDER = ["Safe D", "Likely D", "Lean D", "Toss Up", "Lean R", "Likely R", "Safe R"]
RATING_COLOR = {
    "Safe D": "#1e3a8a", "Likely D": "#1d4ed8", "Lean D": "#93c5fd",
    "Toss Up": "#d1d5db",
    "Lean R": "#fca5a5", "Likely R": "#dc2626", "Safe R": "#7f1d1d",
}
RATING_RE = r"(Toss Up|Lean D|Lean R|Likely D|Likely R|Safe D|Safe R)"

SENATE_NOT_UP = {"D": 34, "R": 31}  # incl. 2 independents caucusing with Democrats

DISCLAIMER = (
    "This is only a collection of publicly available resources and forecasts. "
    "The creator is neutral and does not support any political party, candidate or ideology."
)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------- fetching
def fetch_text(url: str) -> str | None:
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        print("requests/bs4 not installed; skipping fetch", file=sys.stderr)
        return None
    import time
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, headers=headers)
            if r.status_code in (429, 503):
                wait = int(r.headers.get("Retry-After", "0") or 0) or 20 * (attempt + 1)
                print(f"  {r.status_code} from {url}; retrying in {wait}s", file=sys.stderr)
                time.sleep(min(wait, 60))
                continue
            r.raise_for_status()
            break
        except Exception as e:  # network, 4xx, 5xx
            print(f"  fetch failed {url}: {e}", file=sys.stderr)
            r = None
            break
    else:
        r = None
    if r is None or not r.ok:
        return None
    time.sleep(3)  # be polite between requests
    soup = BeautifulSoup(r.text, "lxml")
    meta = " ".join(
        m.get("content", "")
        for m in soup.find_all("meta")
        if m.get("name") in ("description", "og:description", "twitter:description")
        or m.get("property") in ("og:description",)
    )
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = meta + " " + soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def extract(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text, flags=re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 <= v <= 100 else None


def update_chambers(sources: dict, latest: dict, offline: bool) -> None:
    today = now_utc().date().isoformat()
    for src in sources["chamber_sources"]:
        rec = latest["chambers"].setdefault(src["id"], {"house": None, "senate": None})
        if offline:
            continue
        print(f"Fetching {src['name']} ...")
        text = fetch_text(src["url"])
        got = False
        if text:
            for chamber in ("house", "senate"):
                v = extract(src.get(f"{chamber}_regex", "$^"), text)
                if v is not None:
                    rec[chamber] = v
                    got = True
        if got:
            rec["as_of"] = today
            rec["status"] = "live"
        else:
            rec["status"] = "stale" if rec.get("status") in ("live", "stale", "seed") else "unavailable"
        print(f"  -> house={rec.get('house')} senate={rec.get('senate')} status={rec['status']}")


def update_races(csv_path: Path, url: str, offline: bool) -> bool:
    rows = list(csv.DictReader(csv_path.open()))
    if offline:
        return False
    print(f"Fetching races {url} ...")
    text = fetch_text(url)
    if not text:
        return False
    changed = 0
    for row in rows:
        pat = re.escape(row["state"]) + r"\s+(\d{1,3})%\s+" + RATING_RE
        m = re.search(pat, text)
        if m:
            p, rating = m.group(1), m.group(2)
            if row["ddhq_prob"] != p or row["ddhq_rating"] != rating:
                changed += 1
            row["ddhq_prob"], row["ddhq_rating"] = p, rating
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"  -> {changed} race(s) changed")
    return changed > 0


# ---------------------------------------------------------------- analysis
def dem_prob(row: dict) -> float:
    """Probability the Democratic side (or D-aligned) wins; 0.5 for pure toss-ups
    where the source does not name the leader."""
    p = float(row["ddhq_prob"]) / 100
    r = row["ddhq_rating"]
    if r.endswith(" D"):
        return p
    if r.endswith(" R"):
        return 1 - p
    return 0.5


def ensemble(latest: dict, chamber: str) -> tuple[float | None, int]:
    vals = [v[chamber] for v in latest["chambers"].values() if v.get(chamber) is not None and v.get("status") != "unavailable"]
    if not vals:
        return None, 0
    return round(statistics.mean(vals), 1), len(vals)


def senate_projection(rows: list[dict]) -> dict:
    exp_d = SENATE_NOT_UP["D"] + sum(dem_prob(r) for r in rows)
    counts = {k: 0 for k in RATING_ORDER}
    for r in rows:
        counts[r["ddhq_rating"]] += 1
    return {"expected_d": round(exp_d, 1), "expected_r": round(100 - exp_d, 1), "counts": counts}


# ---------------------------------------------------------------- charts
def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID,
        "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
        "svg.fonttype": "none", "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    })
    return plt


def chart_infographic(latest, house, senate, proj, ts):
    plt = _mpl()
    fig = plt.figure(figsize=(12, 4.2))
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off"); ax.set_xlim(0, 12); ax.set_ylim(0, 4.2)
    days = (dt.date(2026, 11, 3) - now_utc().date()).days
    when = "Election Day: Tuesday, Nov 3, 2026" if days >= 0 else "Election held Nov 3, 2026: check official results"
    ax.text(0.4, 3.75, "2026 US MIDTERMS: LIVE FORECAST AVERAGE", fontsize=17, weight="bold", color=INK)
    ax.text(0.4, 3.35, f"{when}   |   updated {ts} UTC", fontsize=10.5, color=MUTED)

    def card(x, title, d_val, n, sub):
        ax.add_patch(plt.Rectangle((x, 0.45), 3.6, 2.55, color="#f9fafb", ec=GRID, lw=1))
        ax.text(x + 0.25, 2.6, title, fontsize=12, weight="bold", color=INK)
        if d_val is None:
            ax.text(x + 0.25, 1.6, "n/a", fontsize=30, color=MUTED); return
        r_val = 100 - d_val
        ax.text(x + 0.25, 1.55, f"D {d_val:.0f}%", fontsize=28, weight="bold", color=D_BLUE)
        ax.text(x + 3.35, 1.55, f"R {r_val:.0f}%", fontsize=28, weight="bold", color=R_RED, ha="right")
        bx, bw = x + 0.25, 3.1
        ax.add_patch(plt.Rectangle((bx, 1.05), bw * d_val / 100 - 0.02, 0.28, color=D_BLUE))
        ax.add_patch(plt.Rectangle((bx + bw * d_val / 100 + 0.02, 1.05), bw * r_val / 100 - 0.02, 0.28, color=R_RED))
        ax.text(x + 0.25, 0.65, sub.format(n=n), fontsize=9.5, color=MUTED)

    card(0.4, "Chance of controlling the HOUSE", house[0], house[1], "weighted consensus of {n} fresh forecasts")
    card(4.2, "Chance of controlling the SENATE", senate[0], senate[1], "weighted consensus of {n} fresh forecasts")
    ax.add_patch(plt.Rectangle((8.0, 0.45), 3.6, 2.55, color="#f9fafb", ec=GRID, lw=1))
    ax.text(8.25, 2.6, "Senate seat projection", fontsize=12, weight="bold", color=INK)
    ax.text(8.25, 1.55, f"{proj['expected_d']:.0f}", fontsize=28, weight="bold", color=D_BLUE)
    ax.text(9.0, 1.62, "D  vs", fontsize=13, color=MUTED)
    ax.text(9.85, 1.55, f"{proj['expected_r']:.0f}", fontsize=28, weight="bold", color=R_RED)
    ax.text(10.6, 1.62, "R", fontsize=13, color=MUTED)
    ax.text(8.25, 1.1, f"{proj['counts']['Toss Up']} toss-ups  |  51 for majority*", fontsize=10, color=INK)
    ax.text(8.25, 0.65, "*50-50 tie goes to Republicans (VP vote)", fontsize=9.5, color=MUTED)
    ax.text(0.4, 0.12, DISCLAIMER, fontsize=8.5, color=MUTED, style="italic")
    fig.savefig(ASSETS / "infographic.svg"); plt.close(fig)


def chart_chambers(latest, names=None):
    plt = _mpl()
    rows = [(k, r) for k, r in latest["chambers"].items() if r.get("type") in SE.WEIGHTS and (r.get("house") is not None or r.get("senate") is not None)]
    rows.sort(key=lambda kr: (list(SE.WEIGHTS).index(kr[1]["type"]), kr[1]["name"]))
    n = max(len(rows), 1)
    fig, axes = plt.subplots(1, 2, figsize=(13, 0.45 * n + 1.8), sharey=True)
    for ax, chamber in zip(axes, ("house", "senate")):
        labels = []
        for i, (k, r) in enumerate(rows):
            fresh = SE.is_fresh(r)
            labels.append(r["name"][:34] + ("" if fresh else " (old, excluded)"))
            v = r.get(chamber)
            if v is None:
                ax.text(2, i, "no public number", va="center", color=MUTED, fontsize=9)
                continue
            a = 1.0 if fresh else 0.35
            ax.barh(i, v, color=D_BLUE, height=0.6, alpha=a)
            ax.barh(i, 100 - v, left=v, color=R_RED, height=0.6, alpha=a)
            ax.text(min(v, 96) - 1.5, i, f"{v:.0f}%", va="center", ha="right", color="white", fontsize=9, weight="bold")
        ax.axvline(50, color=INK, lw=1, ls=(0, (3, 3)))
        ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels, fontsize=9); ax.invert_yaxis()
        ax.set_xlim(0, 100); ax.set_xlabel("Dem chance of control (%)  |  blue = D, red = R")
        ax.set_title(f"{chamber.title()}: every forecast with a public number", loc="left", weight="bold", color=INK)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
    fig.text(0.01, 0.005, f"Faded = older than {SE.FRESH_DAYS} days, excluded from consensus. Models, prediction markets and crowd forecasts. " + DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(ASSETS / "chamber_probabilities.svg"); plt.close(fig)


def chart_generic_ballot(gb):
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(10, 3.6))
    groups = gb.get("by_group", {})
    order = [g for g in ("Republican-leaning", "Nonpartisan", "Democratic-leaning", "Unclassified") if g in groups]
    if not order:
        ax.text(0.5, 0.5, "Generic-ballot poll table not available yet", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    for i, g in enumerate(order):
        m = groups[g]["avg_margin"]
        ax.barh(i, m, color=D_BLUE if m >= 0 else R_RED, height=0.55)
        ax.text(m + (0.3 if m >= 0 else -0.3), i, f"{'D' if m >= 0 else 'R'}+{abs(m):.1f}  ({groups[g]['n']} polls)", va="center", ha="left" if m >= 0 else "right", fontsize=10, color=INK)
    ax.axvline(0, color=INK, lw=1)
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order); ax.invert_yaxis()
    lim = max([abs(groups[g]["avg_margin"]) for g in order] + [5]) + 4
    ax.set_xlim(-lim, lim); ax.set_xlabel("Generic ballot margin (points): right = Democratic lead, left = Republican lead")
    ax.set_title("Generic congressional ballot, grouped by pollster lean", loc="left", weight="bold", color=INK)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.text(0.01, 0.005, "Source: Wikipedia nationwide poll table (auto-updated). Lean = common characterization or partisan clients, not accuracy. " + DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(ASSETS / "generic_ballot_by_lean.svg"); plt.close(fig)


def chart_races(rows, title, fname, max_rows=14):
    plt = _mpl()
    comp = [r for r in rows if r["ddhq_rating"] not in ("Safe D", "Safe R")]
    comp.sort(key=lambda r: abs(dem_prob(r) - 0.5))
    comp = comp[:max_rows]
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(comp) + 1.4))
    for i, r in enumerate(comp):
        pd_ = dem_prob(r) * 100
        ax.barh(i, pd_, color=D_BLUE, height=0.6)
        ax.barh(i, 100 - pd_, left=pd_, color=R_RED, height=0.6)
        lbl = r["ddhq_rating"] if r["ddhq_rating"] != "Toss Up" else "Toss Up (leader not named)"
        ax.text(101, i, lbl, va="center", fontsize=9.5, color=INK)
    ax.axvline(50, color=INK, lw=1, ls=(0, (3, 3)))
    ax.set_yticks(range(len(comp))); ax.set_yticklabels([r["state"] for r in comp]); ax.invert_yaxis()
    ax.set_xlim(0, 100); ax.set_xlabel("Win probability (%)  |  blue = Democratic side, red = Republican side")
    ax.set_title(title, loc="left", weight="bold", color=INK)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.text(0.01, 0.005, "Source: Decision Desk HQ forecast (auto-updated). " + DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 0.86, 1))
    fig.savefig(ASSETS / fname); plt.close(fig)


def chart_hemicycle(rows):
    plt = _mpl()
    seats = ["Not up: D"] * SENATE_NOT_UP["D"]
    for rating in RATING_ORDER:
        seats += [rating] * sum(1 for r in rows if r["ddhq_rating"] == rating)
    seats += ["Not up: R"] * SENATE_NOT_UP["R"]
    colors = {**RATING_COLOR, "Not up: D": "#3b5bdb", "Not up: R": "#c92a2a"}
    rows_n, pts = 5, []
    radii = [1.0 + 0.32 * i for i in range(rows_n)]
    total_len = sum(radii)
    per = [round(100 * r / total_len) for r in radii]
    per[-1] += 100 - sum(per)
    for r, n in zip(radii, per):
        for k in range(n):
            a = math.pi * (1 - k / (n - 1))
            pts.append((a, r * math.cos(a), r * math.sin(a)))
    pts.sort(key=lambda t: -t[0])
    fig, ax = plt.subplots(figsize=(9, 5))
    for (a, x, y), s in zip(pts, seats):
        if s.startswith("Not up"):
            ax.scatter(x, y, s=150, facecolors="none", edgecolors=colors[s], linewidths=2.2)
        else:
            ax.scatter(x, y, s=170, color=colors[s], edgecolors=SURFACE, linewidths=1.5)
    ax.text(0, 0.25, "51 for majority", ha="center", fontsize=12, weight="bold", color=INK)
    ax.text(0, 0.02, "Senate after Nov 3 by current rating", ha="center", fontsize=9.5, color=MUTED)
    ax.set_aspect("equal"); ax.axis("off")
    from matplotlib.patches import Patch
    order = ["Not up: D"] + RATING_ORDER + ["Not up: R"]
    ax.legend(handles=[Patch(facecolor="none" if k.startswith("Not up") else colors[k], edgecolor=colors[k], linewidth=2, label=f"{k} ({seats.count(k)})") for k in order],
              loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=5, frameon=False, fontsize=8.5)
    ax.set_title("Senate seat map: 65 seats not up + 35 races by rating", loc="left", weight="bold", color=INK)
    fig.text(0.01, 0.005, DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(ASSETS / "senate_seat_map.svg"); plt.close(fig)


def chart_history():
    plt = _mpl()
    path = DATA / "history.csv"
    rows = list(csv.DictReader(path.open())) if path.exists() else []
    fig, ax = plt.subplots(figsize=(10, 3.8))
    if rows:
        xs = [dt.datetime.fromisoformat(r["timestamp_utc"].replace("Z", "+00:00")) for r in rows]
        for key, color, lbl in (("house_avg", D_BLUE, "House: Dem chance (avg)"), ("senate_avg", "#7c3aed", "Senate: Dem chance (avg)")):
            ys = [float(r[key]) if r[key] else None for r in rows]
            ax.plot(xs, ys, color=color, lw=2, marker="o", ms=4, label=lbl)
            if ys[-1] is not None:
                ax.annotate(f"{ys[-1]:.0f}%", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points", va="center", color=INK, fontsize=10)
    ax.axhline(50, color=MUTED, lw=1, ls=(0, (3, 3)))
    ax.set_ylim(0, 100); ax.set_ylabel("%"); ax.grid(axis="y", color=GRID)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, loc="lower left")
    ax.set_title("Forecast average over time (each auto-update adds a point)", loc="left", weight="bold", color=INK)
    fig.autofmt_xdate()
    fig.text(0.01, 0.005, "Below 50% means Republicans are favored. " + DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(ASSETS / "history.svg"); plt.close(fig)


# ---------------------------------------------------------------- README block
def readme_block(latest, names, urls, house, senate, proj, sen_rows, gov_rows, ts, hc=None, sc=None, gb=None, concl=None):
    def fmt(v):
        return "n/a" if v is None else f"{v:.1f}%"
    hc, sc, gb, concl = hc or {}, sc or {}, gb or {}, concl or []
    lines = [
        f"_Last automatic update: **{ts} UTC**. Markets refresh every minute, headlines every 5 minutes, everything else every 15 minutes (GitHub Actions)._",
        "",
        "### 🧾 Conclusion (auto-generated from all sources)",
        "",
    ] + [f"- {c}" for c in concl] + [
        "",
        "| Consensus (weighted) | Democrats | Republicans | Fresh sources | Range across sources | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for label, c in (("**House control**", hc), ("**Senate control**", sc)):
        v = c.get("value")
        rng = f"{c['min']['value']:.0f}% to {c['max']['value']:.0f}%" if v is not None else "-"
        lines.append(f"| {label} | {fmt(v)} | {fmt(None if v is None else 100 - v)} | {c.get('n', 0)} | {rng} | {SE.verdict(v)} |")
    lines.append(f"| **Senate seats (expected)** | {proj['expected_d']} | {proj['expected_r']} | DDHQ race odds | - | 51 needed |")
    if gb.get("by_group"):
        lines += ["", "| Generic ballot by pollster lean | Avg margin | Polls |", "|---|---|---|"]
        for g, v in sorted(gb["by_group"].items()):
            lines.append(f"| {g} | {'D' if v['avg_margin'] >= 0 else 'R'}+{abs(v['avg_margin']):.1f} | {v['n']} |")
    lines += ["", "**Forecasts in the consensus**", "", "| Source | Type | House (D) | Senate (D) | As of |", "|---|---|---|---|---|"]
    for k, r in sorted(latest["chambers"].items(), key=lambda kr: (list(SE.WEIGHTS).index(kr[1].get("type", "forecast_model")) if kr[1].get("type") in SE.WEIGHTS else 9, kr[1].get("name", ""))):
        if r.get("type") in SE.WEIGHTS and SE.is_fresh(r) and (r.get("house") is not None or r.get("senate") is not None):
            lines.append(f"| [{r['name']}]({r['url']}) | {SE.TYPE_LABEL[r['type']]} | {fmt(r.get('house'))} | {fmt(r.get('senate'))} | {r.get('as_of','')} |")
    lines += ["", f"All {len(json.loads((DATA / 'source_registry.json').read_text())['sources'])} sources, grouped by type (models, markets, raters, pollsters, news, TV, social, academic, AI, repos): **[docs/09](docs/09-all-sources-consensus.md)**."]
    lines += ["", "**Closest Senate races right now**", "", "| State | Democrat | Republican / other | Rating | Win prob. (favored side) |", "|---|---|---|---|---|"]
    close = sorted([r for r in sen_rows if r["ddhq_rating"] not in ("Safe D", "Safe R")], key=lambda r: abs(dem_prob(r) - 0.5))[:10]
    for r in close:
        other = r["rep_candidate"] + (f"; {r['other_candidate']}" if r["other_candidate"] else "")
        lines.append(f"| {r['state']} | {r['dem_candidate'] or '-'} | {other} | {r['ddhq_rating']} | {r['ddhq_prob']}% |")
    try:
        hl = json.loads((DATA / "headlines.json").read_text())
        us = hl.get("countries", {}).get("United States", [])[:5]
        if us:
            lines += ["", "**Latest midterm headlines (auto-updated)**", ""] + [f"- [{h['title']}]({h['url']}) ({h['source']})" for h in us]
            lines += ["", "Headlines from all 16 countries: [docs/08](docs/08-global-media-and-research-sources.md) and the dashboard's World media tab."]
    except Exception:
        pass
    lines += ["", "> Forecasts are probabilities, not results. On and after Nov 3, follow official state results and AP / DDHQ race calls."]
    return "\n".join(lines)


def write_readme(block: str) -> None:
    txt = README.read_text()
    start, end = "<!-- LIVE:START -->", "<!-- LIVE:END -->"
    if start in txt and end in txt:
        pre = txt.split(start)[0]
        post = txt.split(end)[1]
        README.write_text(pre + start + "\n" + block + "\n" + end + post)



# ---------------------------------------------------------------- live headlines
def gnews_url(feed: dict) -> str:
    from urllib.parse import quote
    return (f"https://news.google.com/rss/search?q={quote(feed['q'])}+when:7d"
            f"&hl={feed['hl']}&gl={feed['gl']}&ceid={feed['ceid']}")


def fetch_feed(feed: dict, limit: int = 8) -> list[dict] | None:
    try:
        import requests
        import xml.etree.ElementTree as ET
        r = requests.get(gnews_url(feed), timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"  feed failed {feed.get('q')}: {e}", file=sys.stderr)
        return None
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        title = (it.findtext("title") or "").strip()
        source = src.text.strip() if src is not None and src.text else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        out.append({
            "title": title,
            "url": (it.findtext("link") or "").strip(),
            "source": source,
            "source_url": src.get("url", "") if src is not None else "",
            "published": (it.findtext("pubDate") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def update_headlines(offline: bool, candidates: bool = False) -> dict:
    path = DATA / "headlines.json"
    prev = json.loads(path.read_text()) if path.exists() else {"countries": {}, "topics": {}}
    if offline:
        return prev
    media = json.loads((DATA / "world_media.json").read_text())
    res = {"updated_utc": now_utc().isoformat().replace("+00:00", "Z"), "countries": {}, "topics": {},
           "note": "Headlines are titles and links only, pulled automatically from Google News country editions. Inclusion is not endorsement."}
    for c in media["countries"]:
        items = fetch_feed(c["news_feed"])
        res["countries"][c["country"]] = items if items is not None else prev.get("countries", {}).get(c["country"], [])
        print(f"  headlines {c['country']}: {len(res['countries'][c['country']])}")
    for k, f in media.get("topic_feeds", {}).items():
        items = fetch_feed(f)
        res["topics"][k] = {"label": f["label"], "items": items if items is not None else prev.get("topics", {}).get(k, {}).get("items", [])}
    # campaign trail: star campaigners and party feeds (every headlines run)
    base = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    camp = json.loads((DATA / "campaign.json").read_text())
    res["campaign"] = {}
    for s in camp["star_campaigners"]:
        items = fetch_feed({**base, **s["feed"]}, limit=6)
        res["campaign"][s["name"]] = items if items is not None else prev.get("campaign", {}).get(s["name"], [])
    for party, f in camp["party_feeds"].items():
        items = fetch_feed({**base, "q": f["q"]}, limit=8)
        res["campaign"]["party_" + party] = items if items is not None else prev.get("campaign", {}).get("party_" + party, [])
    # battleground candidates (full runs only; carried over otherwise)
    res["candidates"] = prev.get("candidates", {})
    if candidates:
        for fname, office in (("senate_2026.csv", "Senate"), ("governors_2026.csv", "governor")):
            rows = [r for r in csv.DictReader((DATA / fname).open()) if r["ddhq_rating"] not in ("Safe D", "Safe R")]
            for r in rows:
                for col in ("dem_candidate", "rep_candidate", "other_candidate"):
                    name = re.sub(r"\s*\(.*?\)", "", r.get(col, "") or "").strip()
                    if not name:
                        continue
                    items = fetch_feed({**base, "q": f'"{name}" {r["state"]} {office}'}, limit=5)
                    if items is not None:
                        res["candidates"][name] = items
        print(f"  candidate feeds: {len(res['candidates'])}")
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    return res


def build_media_doc(headlines: dict) -> None:
    media = json.loads((DATA / "world_media.json").read_text())
    rk = media["rankings"]
    L = ["# 8. Global Media, Social and Research Sources (16 countries)", "",
         "[Back to README](../README.md)", "",
         "How the US midterms are covered in the **10 most populous countries** and the **10 largest economies** (16 countries combined).",
         f"Population ranking: {', '.join(rk['population_top10'])} ([source]({rk['population_source']})).",
         f"Largest economies (nominal GDP, IMF): {', '.join(rk['gdp_top10'])} ([source]({rk['gdp_source']})). {rk['gdp_note']}", "",
         "> This file is regenerated automatically from [`data/world_media.json`](../data/world_media.json) and [`data/headlines.json`](../data/headlines.json) on every live update.",
         f"> Latest headlines refreshed: **{headlines.get('updated_utc', 'not yet')}**.", "",
         "**Global wire services:** " + ", ".join(f"[{w['name']}]({w['url']})" for w in media["global_wires"]), "",
         "## Contents", ""]
    L += [f"- [{c['flag']} {c['country']}](#{c['country'].lower().replace(' ', '-')})" for c in media["countries"]]
    for c in media["countries"]:
        L += ["", f"## {c['country']}", "", f"{c['flag']} {c['why']} | Languages: {c['language']}", ""]
        if c.get("ownership_note"):
            L += [f"_Note: {c['ownership_note']}_", ""]
        for key, label in (("tv", "TV"), ("print", "Print and online"), ("social", "Social media"), ("research", "Research and think tanks"), ("coverage_2026", "Verified 2026 midterm coverage")):
            if c.get(key):
                L.append(f"**{label}:** " + " · ".join(f"[{x['name']}]({x['url']})" for x in c[key]))
                L.append("")
        items = headlines.get("countries", {}).get(c["country"], [])[:5]
        if items:
            L.append("**Latest headlines (auto-updated):**")
            L += [f"- [{h['title']}]({h['url']}) ({h['source']})" for h in items]
            L.append("")
    if headlines.get("topics"):
        L += ["", "## Live topic feeds", ""]
        for k, tf in headlines["topics"].items():
            L += [f"### {tf['label']}", ""] + [f"- [{h['title']}]({h['url']}) ({h['source']})" for h in tf["items"][:6]] + [""]
    L += ["---", "", "> **Disclaimer:** " + DISCLAIMER + " Media listed here represent a range of ownership models and viewpoints, including state-controlled outlets, which are labeled. Inclusion is not endorsement."]
    (ROOT / "docs" / "08-global-media-and-research-sources.md").write_text("\n".join(L) + "\n")


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--mode", choices=["full", "fast", "headlines"], default="full",
                    help="fast = markets + consensus only (for 1-minute loop); headlines = fast + news feeds; full = everything")
    args = ap.parse_args()
    fast = args.mode in ("fast", "headlines")
    ASSETS.mkdir(exist_ok=True)

    sources = json.loads((DATA / "sources.json").read_text())
    latest = json.loads((DATA / "latest.json").read_text())
    names = {s["id"]: s["name"] for s in sources["chamber_sources"]}
    urls = {s["id"]: s["url"] for s in sources["chamber_sources"]}
    for s in sources["chamber_sources"]:
        latest["chambers"].setdefault(s["id"], {})["type"] = s["type"]

    SE.update_registry_sources(latest, fetch_text, args.offline, only_fast=fast)
    gb = SE.generic_ballot(args.offline or fast)
    if args.mode in ("full", "headlines"):
        headlines = update_headlines(args.offline, candidates=(args.mode == "full"))
        build_media_doc(headlines)
    if args.mode == "full":
        update_races(DATA / "senate_2026.csv", sources["race_sources"]["senate"]["url"], args.offline)
        update_races(DATA / "governors_2026.csv", sources["race_sources"]["governor"]["url"], args.offline)

    sen = list(csv.DictReader((DATA / "senate_2026.csv").open()))
    gov = list(csv.DictReader((DATA / "governors_2026.csv").open()))
    hc, sc = SE.consensus(latest, "house"), SE.consensus(latest, "senate")
    house, senate = (hc.get("value"), hc.get("n", 0)), (sc.get("value"), sc.get("n", 0))
    concl = SE.conclusion(hc, sc, gb)
    proj = senate_projection(sen)
    ts_dt = now_utc()
    ts = ts_dt.strftime("%Y-%m-%d %H:%M")

    latest["updated_utc"] = ts_dt.isoformat().replace("+00:00", "Z")
    latest["ensemble"] = {"house_dem": house[0], "house_n": house[1], "senate_dem": senate[0], "senate_n": senate[1]}
    latest["consensus"] = {"house": hc, "senate": sc, "conclusion": concl}
    latest["senate_projection"] = proj
    latest["disclaimer"] = DISCLAIMER
    (DATA / "latest.json").write_text(json.dumps(latest, indent=2) + "\n")

    hist = DATA / "history.csv"
    new = not hist.exists()
    prev = list(csv.DictReader(hist.open())) if not new else []
    row = {"timestamp_utc": latest["updated_utc"], "house_avg": house[0], "senate_avg": senate[0],
           "senate_expected_d": proj["expected_d"], "n_house": house[1], "n_senate": senate[1]}
    same = prev and prev[-1]["house_avg"] == str(house[0]) and prev[-1]["senate_avg"] == str(senate[0])
    if not same:
        with hist.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=row.keys())
            if new:
                w.writeheader()
            w.writerow(row)

    if fast:
        print(f"Fast update done. House={house}, Senate={senate}")
        return
    chart_infographic(latest, house, senate, proj, ts)
    chart_chambers(latest)
    chart_generic_ballot(gb)
    chart_races(sen, "Senate: most competitive races", "senate_battlegrounds.svg")
    chart_races(gov, "Governors: most competitive races", "governor_battlegrounds.svg")
    chart_hemicycle(sen)
    chart_history()
    write_readme(readme_block(latest, names, urls, house, senate, proj, sen, gov, ts, hc, sc, gb, concl))
    SE.build_registry_doc(latest, hc, sc, gb, concl, DISCLAIMER)
    print(f"Done. House avg={house}, Senate avg={senate}, Senate expected D={proj['expected_d']}")


if __name__ == "__main__":
    main()
