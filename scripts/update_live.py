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
    try:
        r = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; us-midterm-elections-2026-/1.0; +https://github.com/AravindB98)",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        r.raise_for_status()
    except Exception as e:  # network, 4xx, 5xx
        print(f"  fetch failed {url}: {e}", file=sys.stderr)
        return None
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

    card(0.4, "Chance of controlling the HOUSE", house[0], house[1], "average of {n} public forecasts")
    card(4.2, "Chance of controlling the SENATE", senate[0], senate[1], "average of {n} public forecasts")
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


def chart_chambers(latest, names):
    plt = _mpl()
    ids = [k for k in names if k in latest["chambers"]]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), sharey=True)
    for ax, chamber in zip(axes, ("house", "senate")):
        labels, vals = [], []
        for k in ids:
            v = latest["chambers"][k].get(chamber)
            labels.append(names[k] + (" *" if latest["chambers"][k].get("status") == "stale" else ""))
            vals.append(v)
        y = range(len(labels))
        for i, v in enumerate(vals):
            if v is None:
                ax.text(2, i, "no public number", va="center", color=MUTED, fontsize=9.5)
                continue
            ax.barh(i, v, color=D_BLUE, height=0.55)
            ax.barh(i, 100 - v, left=v, color=R_RED, height=0.55)
            ax.text(v - 1.5, i, f"D {v:.0f}%", va="center", ha="right", color="white", fontsize=10, weight="bold")
        ax.axvline(50, color=INK, lw=1, ls=(0, (3, 3)))
        ax.set_yticks(list(y)); ax.set_yticklabels(labels); ax.invert_yaxis()
        ax.set_xlim(0, 100); ax.set_xlabel("Chance of control (%)  |  blue = Democrats, red = Republicans")
        ax.set_title(f"{chamber.title()} control by forecaster", loc="left", weight="bold", color=INK)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
    fig.text(0.01, 0.01, "* stale: last good value kept because the source could not be read on the latest run.  " + DISCLAIMER, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(ASSETS / "chamber_probabilities.svg"); plt.close(fig)


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
def readme_block(latest, names, urls, house, senate, proj, sen_rows, gov_rows, ts):
    def fmt(v):
        return "n/a" if v is None else f"{v:.1f}%"
    lines = [
        f"_Last automatic update: **{ts} UTC**. Refreshed hourly (every 15 minutes on Election Day) by GitHub Actions._",
        "",
        "| Forecast average | Democrats | Republicans | Sources averaged |",
        "|---|---|---|---|",
        f"| **House control** | {fmt(house[0])} | {fmt(None if house[0] is None else 100 - house[0])} | {house[1]} |",
        f"| **Senate control** | {fmt(senate[0])} | {fmt(None if senate[0] is None else 100 - senate[0])} | {senate[1]} |",
        f"| **Senate seats (expected)** | {proj['expected_d']} | {proj['expected_r']} | DDHQ race probabilities |",
        "",
        "| Source | Type | House (D) | Senate (D) | As of | Status |",
        "|---|---|---|---|---|---|",
    ]
    for k, name in names.items():
        r = latest["chambers"].get(k, {})
        lines.append(f"| [{name}]({urls[k]}) | {r.get('type','')} | {fmt(r.get('house'))} | {fmt(r.get('senate'))} | {r.get('as_of','')} | {r.get('status','')} |")
    lines += ["", "**Closest Senate races right now**", "", "| State | Democrat | Republican / other | Rating | Win prob. (favored side) |", "|---|---|---|---|---|"]
    close = sorted([r for r in sen_rows if r["ddhq_rating"] not in ("Safe D", "Safe R")], key=lambda r: abs(dem_prob(r) - 0.5))[:10]
    for r in close:
        other = r["rep_candidate"] + (f"; {r['other_candidate']}" if r["other_candidate"] else "")
        lines.append(f"| {r['state']} | {r['dem_candidate'] or '-'} | {other} | {r['ddhq_rating']} | {r['ddhq_prob']}% |")
    lines += ["", "**Closest governor races right now**", "", "| State | Rating | Win prob. (favored side) |", "|---|---|---|"]
    gclose = sorted([r for r in gov_rows if r["ddhq_rating"] not in ("Safe D", "Safe R")], key=lambda r: abs(dem_prob(r) - 0.5))[:8]
    for r in gclose:
        lines.append(f"| {r['state']} | {r['ddhq_rating']} | {r['ddhq_prob']}% |")
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


def update_headlines(offline: bool) -> dict:
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
    args = ap.parse_args()
    ASSETS.mkdir(exist_ok=True)

    sources = json.loads((DATA / "sources.json").read_text())
    latest = json.loads((DATA / "latest.json").read_text())
    names = {s["id"]: s["name"] for s in sources["chamber_sources"]}
    urls = {s["id"]: s["url"] for s in sources["chamber_sources"]}
    for s in sources["chamber_sources"]:
        latest["chambers"].setdefault(s["id"], {})["type"] = s["type"]

    update_chambers(sources, latest, args.offline)
    headlines = update_headlines(args.offline)
    build_media_doc(headlines)
    update_races(DATA / "senate_2026.csv", sources["race_sources"]["senate"]["url"], args.offline)
    update_races(DATA / "governors_2026.csv", sources["race_sources"]["governor"]["url"], args.offline)

    sen = list(csv.DictReader((DATA / "senate_2026.csv").open()))
    gov = list(csv.DictReader((DATA / "governors_2026.csv").open()))
    house, senate = ensemble(latest, "house"), ensemble(latest, "senate")
    proj = senate_projection(sen)
    ts_dt = now_utc()
    ts = ts_dt.strftime("%Y-%m-%d %H:%M")

    latest["updated_utc"] = ts_dt.isoformat().replace("+00:00", "Z")
    latest["ensemble"] = {"house_dem": house[0], "house_n": house[1], "senate_dem": senate[0], "senate_n": senate[1]}
    latest["senate_projection"] = proj
    latest["disclaimer"] = DISCLAIMER
    (DATA / "latest.json").write_text(json.dumps(latest, indent=2) + "\n")

    hist = DATA / "history.csv"
    new = not hist.exists()
    prev = list(csv.DictReader(hist.open())) if not new else []
    row = {"timestamp_utc": latest["updated_utc"], "house_avg": house[0], "senate_avg": senate[0],
           "senate_expected_d": proj["expected_d"], "n_house": house[1], "n_senate": senate[1]}
    same = prev and prev[-1]["house_avg"] == str(house[0]) and prev[-1]["senate_avg"] == str(senate[0]) \
        and prev[-1]["timestamp_utc"][:10] == row["timestamp_utc"][:10]
    if not same:
        with hist.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=row.keys())
            if new:
                w.writeheader()
            w.writerow(row)

    chart_infographic(latest, house, senate, proj, ts)
    chart_chambers(latest, names)
    chart_races(sen, "Senate: most competitive races", "senate_battlegrounds.svg")
    chart_races(gov, "Governors: most competitive races", "governor_battlegrounds.svg")
    chart_hemicycle(sen)
    chart_history()
    write_readme(readme_block(latest, names, urls, house, senate, proj, sen, gov, ts))
    print(f"Done. House avg={house}, Senate avg={senate}, Senate expected D={proj['expected_d']}")


if __name__ == "__main__":
    main()
