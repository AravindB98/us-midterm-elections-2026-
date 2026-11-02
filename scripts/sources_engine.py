"""
Multi-source engine: reads every forecast source in data/source_registry.json that
publishes a machine-readable number, pulls the nationwide generic-ballot poll table,
computes a weighted consensus, splits results by source lean, and writes a plain-
language conclusion.

The creator of this repository is neutral and does not support any political party
or ideology. This module only reports what public sources publish.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

WEIGHTS = {"forecast_model": 1.0, "prediction_market": 0.8, "crowd_forecast": 0.4}
FRESH_DAYS = 14  # values older than this are shown but excluded from the consensus
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _get(url: str, **kw):
    import requests
    import time
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, headers=UA, **kw)
            if r.status_code in (429, 503):
                return None
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  GET failed {url}: {e}", file=sys.stderr)
            return None
    return None


# ------------------------------------------------------------ market APIs
def _yes_price(market: dict) -> float | None:
    try:
        prices = market.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        return float(prices[0]) * 100
    except Exception:
        return None


def polymarket() -> dict:
    out = {}
    for chamber in ("House", "Senate"):
        r = _get("https://gamma-api.polymarket.com/public-search", params={"q": f"Democratic Party control the {chamber} 2026", "limit_per_type": 20})
        if not r:
            continue
        try:
            events = r.json().get("events", [])
        except Exception:
            continue
        for ev in events:
            for m in ev.get("markets", []) or []:
                q = (m.get("question") or "").lower()
                if "democratic party" in q and f"control the {chamber.lower()}" in q and "2026" in q and not m.get("closed"):
                    p = _yes_price(m)
                    if p is not None:
                        out[chamber.lower()] = round(p, 1)
                        print(f"    polymarket matched: {m.get('question')} -> {p:.1f}")
                        break
            if chamber.lower() in out:
                break
    return out


def predictit() -> dict:
    r = _get("https://www.predictit.org/api/marketdata/all/")
    out = {}
    if not r:
        return out
    try:
        markets = r.json().get("markets", [])
    except Exception:
        return out
    for m in markets:
        name = (m.get("name") or "").lower()
        for chamber in ("house", "senate"):
            if chamber in name and "2026" in name and ("control" in name or "which party" in name):
                for c in m.get("contracts", []):
                    cn = (c.get("name") or c.get("shortName") or "").lower()
                    if "democrat" in cn:
                        v = c.get("lastTradePrice")
                        if v and chamber not in out:
                            out[chamber] = round(float(v) * 100, 1)
                            print(f"    predictit matched: {m.get('name')} / {cn} -> {out[chamber]}")
    return out


def manifold() -> dict:
    out = {}
    queries = {"house": "Democrats win the House 2026", "senate": "Democrats win the Senate 2026"}
    for chamber, q in queries.items():
        r = _get("https://api.manifold.markets/v0/search-markets", params={"term": q, "sort": "liquidity", "filter": "open", "limit": 20})
        if not r:
            continue
        try:
            ms = r.json()
        except Exception:
            continue
        for m in ms:
            qq = (m.get("question") or "").lower()
            if m.get("outcomeType") == "BINARY" and "2026" in qq and chamber in qq and "democrat" in qq \
                    and not any(w in qq for w in ("both", "and the", "lose", "but", "seats", "if ")):
                out[chamber] = round(float(m.get("probability", 0)) * 100, 1)
                print(f"    manifold matched: {m.get('question')} -> {out[chamber]}")
                break
    return out


API = {"polymarket": polymarket, "predictit": predictit, "manifold": manifold}


# ------------------------------------------------------------ registry update
def update_registry_sources(latest: dict, fetch_text, offline: bool, only_fast: bool = False) -> None:
    reg = json.loads((DATA / "source_registry.json").read_text())["sources"]
    ch = latest.setdefault("chambers", {})
    for s in reg:
        if s["type"] not in WEIGHTS:
            continue
        rec = ch.setdefault(s["id"], {})
        rec.update({"name": s["name"], "type": s["type"], "url": s["url"], "lean": s.get("lean", "")})
        seed = s.get("seed")
        if seed and not rec.get("as_of"):
            rec.update({"house": seed.get("house"), "senate": seed.get("senate"), "as_of": seed.get("as_of"), "status": "seed"})
        if s.get("notes"):
            rec["note"] = s["notes"]
        ex = s.get("extract")
        if only_fast and not (ex and (ex["kind"] in API or s["id"] == "kalshi")):
            continue
        if offline or not ex:
            rec.setdefault("status", "not machine-readable" if not ex else "seed")
            continue
        print(f"Fetching {s['name']} ...")
        got = {}
        if ex["kind"] == "regex":
            text = fetch_text(s["url"])
            if text:
                for c in ("house", "senate"):
                    m = re.search(ex.get(c, "$^"), text, flags=re.I)
                    if m:
                        v = float(m.group(1))
                        if 0 <= v <= 100:
                            got[c] = v
        elif ex["kind"] in API:
            try:
                got = API[ex["kind"]]()
            except Exception as e:
                print(f"  API error {s['id']}: {e}", file=sys.stderr)
        if got:
            rec.update(got)
            rec["as_of"] = today().isoformat()
            rec["status"] = "live"
        elif rec.get("as_of"):
            rec["status"] = "stale"
        else:
            rec["status"] = "unavailable"
        print(f"  -> {got or 'no new value'} ({rec['status']})")


# ------------------------------------------------------------ generic ballot
POLLSTER_LEAN = None


def _lean_for(pollster: str) -> str:
    global POLLSTER_LEAN
    if POLLSTER_LEAN is None:
        reg = json.loads((DATA / "source_registry.json").read_text())["sources"]
        POLLSTER_LEAN = []
        for s in reg:
            if s["type"] == "pollster":
                key = re.sub(r"[^a-z]", "", s["name"].split("(")[0].split("/")[0].lower())
                POLLSTER_LEAN.append((key, s["lean"]))
    p = re.sub(r"[^a-z]", "", pollster.lower())
    for key, lean in POLLSTER_LEAN:
        if key and (key in p or p[:8] and p[:8] in key):
            return lean
    return "Unclassified"


def lean_group(lean: str) -> str:
    l = lean.lower()
    if "republican" in l:
        return "Republican-leaning"
    if "democrat" in l or "progressive" in l:
        return "Democratic-leaning"
    if "unclassified" in l:
        return "Unclassified"
    return "Nonpartisan"


def generic_ballot(offline: bool) -> dict:
    path = DATA / "generic_ballot.json"
    prev = json.loads(path.read_text()) if path.exists() else {}
    if offline:
        return prev
    try:
        import pandas as pd
        from io import StringIO
    except ImportError:
        return prev
    title = None
    candidates = []
    for q in ("Nationwide opinion polling 2026 United States House of Representatives elections",
              "generic ballot opinion polling 2026 United States House",
              "2026 United States House of Representatives elections"):
        r = _get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "format": "json", "srlimit": 8, "srsearch": q})
        if r:
            candidates += [h["title"] for h in r.json().get("query", {}).get("search", [])]
    print(f"  generic ballot: candidate pages {candidates[:8]}")
    pref = [c for c in candidates if "2026" in c and "house" in c.lower() and ("polling" in c.lower() or "opinion" in c.lower())]
    fall = [c for c in candidates if c == "2026 United States House of Representatives elections"]
    title = (pref or fall or [None])[0]
    if not title:
        print("  generic ballot: no Wikipedia page found", file=sys.stderr)
        return prev
    print(f"  generic ballot: using page '{title}'")
    page = _get("https://en.wikipedia.org/w/index.php", params={"title": title, "action": "render"})
    if not page:
        return prev
    polls = []
    try:
        tables = pd.read_html(StringIO(page.text))
    except Exception as e:
        print(f"  generic ballot parse error: {e}", file=sys.stderr)
        return prev
    for t in tables:
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
        t.columns = cols
        pc = next((c for c in cols if re.search(r"poll(ster| source)", c, re.I)), None)
        dc = next((c for c in cols if re.search(r"democrat", c, re.I)), None)
        rc = next((c for c in cols if re.search(r"republican", c, re.I)), None)
        datec = next((c for c in cols if re.search(r"date", c, re.I)), None)
        if not (pc and dc and rc):
            continue
        for _, row in t.iterrows():
            try:
                d = float(re.sub(r"[^\d.]", "", str(row[dc])) or "nan")
                rp = float(re.sub(r"[^\d.]", "", str(row[rc])) or "nan")
            except ValueError:
                continue
            if not (0 < d < 100 and 0 < rp < 100):
                continue
            name = re.sub(r"\[.*?\]", "", str(row[pc])).strip()
            lean = _lean_for(name)
            polls.append({"pollster": name, "dates": str(row[datec]) if datec else "", "dem": d, "rep": rp,
                          "margin": round(d - rp, 1), "lean": lean, "group": lean_group(lean)})
        if polls:
            break
    if not polls:
        print("  generic ballot: no poll rows parsed", file=sys.stderr)
        return prev
    recent = polls[:40]  # Wikipedia lists newest first
    groups = {}
    for p in recent:
        groups.setdefault(p["group"], []).append(p["margin"])
    res = {
        "updated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        "n_recent": len(recent),
        "average_margin": round(statistics.mean(p["margin"] for p in recent), 1),
        "by_group": {g: {"avg_margin": round(statistics.mean(v), 1), "n": len(v)} for g, v in groups.items()},
        "pollsters": sorted({p["pollster"] for p in recent}),
        "recent": recent,
    }
    path.write_text(json.dumps(res, indent=1) + "\n")
    print(f"  generic ballot: {len(recent)} recent polls, avg D{res['average_margin']:+}")
    return res


# ------------------------------------------------------------ consensus
def is_fresh(rec: dict) -> bool:
    try:
        return (today() - dt.date.fromisoformat(rec.get("as_of", ""))).days <= FRESH_DAYS
    except ValueError:
        return False


def consensus(latest: dict, chamber: str) -> dict:
    rows = [(k, r) for k, r in latest.get("chambers", {}).items()
            if r.get("type") in WEIGHTS and r.get(chamber) is not None and is_fresh(r)]
    if not rows:
        return {"value": None, "n": 0}
    w = [WEIGHTS[r["type"]] for _, r in rows]
    v = [r[chamber] for _, r in rows]
    wavg = sum(a * b for a, b in zip(v, w)) / sum(w)
    by_type = {}
    for (_, r), val in zip(rows, v):
        by_type.setdefault(r["type"], []).append(val)
    lo = min(rows, key=lambda kr: kr[1][chamber])
    hi = max(rows, key=lambda kr: kr[1][chamber])
    return {
        "value": round(wavg, 1), "median": round(statistics.median(v), 1), "n": len(rows),
        "min": {"value": lo[1][chamber], "source": lo[1]["name"]}, "max": {"value": hi[1][chamber], "source": hi[1]["name"]},
        "share_favoring_dem": round(100 * sum(1 for x in v if x > 50) / len(v)),
        "stdev": round(statistics.pstdev(v), 1) if len(v) > 1 else 0.0,
        "by_type": {t: round(statistics.mean(x), 1) for t, x in by_type.items()},
        "sources": [k for k, _ in rows],
    }


def verdict(p: float | None) -> str:
    if p is None:
        return "not enough data"
    d = p if p >= 50 else 100 - p
    side = "Democrats" if p >= 50 else "Republicans"
    if d >= 85:
        return f"{side} strongly favored"
    if d >= 65:
        return f"{side} favored"
    if d >= 55:
        return f"{side} slightly favored"
    return "toss-up"


def conclusion(house: dict, senate: dict, gb: dict) -> list[str]:
    out = []
    for name, c in (("House", house), ("Senate", senate)):
        if not c.get("value"):
            out.append(f"{name}: not enough fresh public forecasts to conclude.")
            continue
        sh = c["share_favoring_dem"]
        agree = "every source favors Democrats" if sh == 100 else "every source favors Republicans" if sh == 0 else f"{sh}% of sources favor Democrats"
        s = (f"{name}: {verdict(c['value'])} (weighted average {c['value']:.0f}% Democratic across {c['n']} fresh sources; "
             f"{agree}). Range: {c['min']['value']:.0f}% ({c['min']['source']}) to "
             f"{c['max']['value']:.0f}% ({c['max']['source']}).")
        if c["stdev"] >= 10:
            s += " Sources disagree substantially, so treat this as uncertain."
        out.append(s)
    if gb.get("by_group"):
        parts = [f"{g}: D{v['avg_margin']:+.1f} ({v['n']} polls)" for g, v in sorted(gb["by_group"].items())]
        out.append(f"Generic ballot (last {gb['n_recent']} polls): average D{gb['average_margin']:+.1f}. By pollster lean: " + "; ".join(parts) + ".")
        r = gb["by_group"].get("Republican-leaning")
        if r:
            out.append("Even Republican-leaning pollsters show " + ("a Democratic lead." if r["avg_margin"] > 0 else "a Republican lead or tie.")
                       + " Pollster lean is not a measure of accuracy; it is shown so readers can compare.")
    out.append("Expert raters (Cook, Fox News Power Rankings) see the Senate as closer than most models: Cook calls Democrats slight favorites and Fox rates Senate control a toss-up. "
               "Polling errors of 3 to 5 points in one direction have happened before, which would change the Senate outcome.")
    return out


# ------------------------------------------------------------ registry doc
TYPE_LABEL = {
    "forecast_model": "Forecast models", "prediction_market": "Prediction markets", "crowd_forecast": "Crowd forecasts",
    "aggregator": "Market aggregators (not double-counted)", "expert_rating": "Expert race ratings", "poll_average": "Poll averages",
    "pollster": "Pollsters", "news_print": "News: print and online", "tv": "TV media", "social": "Social media",
    "academic": "Academic research and think tanks", "ai_forecast": "AI models and AI forecasting", "open_source_repo": "Open-source repos and models",
    "satire_comedy": "Comedy, satire and late-night shows", "party_campaign": "Party campaign committees (partisan by definition)",
}


def build_registry_doc(latest: dict, house: dict, senate: dict, gb: dict, concl: list[str], disclaimer: str) -> None:
    reg = json.loads((DATA / "source_registry.json").read_text())["sources"]
    ch = latest.get("chambers", {})
    L = ["# 9. All Sources, Consensus and Conclusion", "", "[Back to README](../README.md)", "",
         f"> Regenerated automatically on every live update. Last run: **{latest.get('updated_utc', '')}**.", "",
         f"**{len(reg)} sources** are tracked below, grouped by type. Forecasts with a fresh public number (last {FRESH_DAYS} days) feed the consensus; "
         "models are weighted 1.0, money-based prediction markets 0.8 and play-money/crowd forecasts 0.4. Older numbers are shown but excluded.", "",
         "## Conclusion (auto-generated)", ""] + [f"- {c}" for c in concl] + ["",
         "## Forecasts used in the consensus", "", "| Source | Type | Lean (as characterized) | House (D) | Senate (D) | As of | Status | In consensus |", "|---|---|---|---|---|---|---|---|"]
    f = lambda v: "-" if v is None else f"{v:.1f}%"
    for k, r in sorted(ch.items(), key=lambda kr: (kr[1].get("type", ""), kr[1].get("name", ""))):
        if r.get("type") not in WEIGHTS:
            continue
        L.append(f"| [{r['name']}]({r['url']}) | {TYPE_LABEL[r['type']]} | {r.get('lean','')} | {f(r.get('house'))} | {f(r.get('senate'))} | {r.get('as_of','-')} | {r.get('status','')} | {'yes' if is_fresh(r) and (r.get('house') is not None or r.get('senate') is not None) else 'no'} |")
    if gb.get("recent"):
        L += ["", f"## Generic ballot polls by pollster lean ([source]({gb['source']}))", "", "| Pollster | Dates | Dem | Rep | Margin | Lean |", "|---|---|---|---|---|---|"]
        for p in gb["recent"][:25]:
            L.append(f"| {p['pollster']} | {p['dates']} | {p['dem']:.0f}% | {p['rep']:.0f}% | D{p['margin']:+.0f} | {p['lean']} |")
    L += ["", "## Every source, by type", ""]
    for t, label in TYPE_LABEL.items():
        items = [s for s in reg if s["type"] == t]
        if not items:
            continue
        L += [f"### {label} ({len(items)})", "", "| Source | Lean (as characterized) | Notes |", "|---|---|---|"]
        for s in items:
            L.append(f"| [{s['name']}]({s['url']}) | {s.get('lean','')} | {s.get('notes', s.get('provides',''))} |")
        L.append("")
    L += ["Lean labels follow common third-party characterizations (AllSides, Ad Fontes Media) or the firm's own partisan clients; they may be disputed and say nothing about accuracy.", "",
          "---", "", f"> **Disclaimer:** {disclaimer}"]
    (ROOT / "docs" / "09-all-sources-consensus.md").write_text("\n".join(L) + "\n")
