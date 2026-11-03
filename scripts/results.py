"""
Election-night results (unofficial) for every 2026 Senate and governor race.

Source: each race's Wikipedia article, read through the MediaWiki API. On election
night Wikipedia editors fill the election infobox (votes, percentages) and set the
winner ("after_election") only once AP or the major networks have called the race.
We report what the infobox says, label it unofficial, and link to official results.

The creator of this repository is neutral and does not support any political party
or ideology. This module only reports what public sources publish.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "us-midterm-elections-2026-dashboard/1.0 (open-source election data project; github.com/AravindB98/us-midterm-elections-2026-)"}

SPECIAL = {"Ohio": "2026 United States Senate special election in Ohio",
           "Florida": "2026 United States Senate special election in Florida"}
PLACEHOLDER = re.compile(r"^(tbd|tba|n/?a|none|\?|—|-)?$", re.I)


def senate_title(state: str) -> str:
    return SPECIAL.get(state, f"2026 United States Senate election in {state}")


def gov_title(state: str) -> str:
    return f"2026 {state} gubernatorial election"


def _clean(v: str) -> str:
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = re.sub(r"\{\{(?:nowrap|small|nobr)\|([^{}]*)\}\}", r"\1", v, flags=re.I)
    v = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"'''?|<br\s*/?>|\{\{[^{}]*\}\}", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def parse_infobox(wikitext: str) -> dict:
    m = re.search(r"\{\{\s*Infobox election(.*?)\n\}\}", wikitext, flags=re.S | re.I)
    if not m:
        return {}
    body = m.group(1)
    fields = {}
    for line in re.split(r"\n\s*\|", body):
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip().lower()] = _clean(v)
    cands = []
    for i in range(1, 6):
        name = fields.get(f"nominee{i}") or fields.get(f"candidate{i}")
        if not name or PLACEHOLDER.match(name):
            continue
        party = fields.get(f"party{i}", "")
        votes = re.sub(r"[^\d]", "", fields.get(f"popular_vote{i}", "") or "")
        pct = re.sub(r"[^\d.]", "", fields.get(f"percentage{i}", "") or "")
        cands.append({"name": name, "party": party, "votes": int(votes) if votes else None,
                      "pct": float(pct) if pct and pct.count(".") <= 1 else None})
    winner = fields.get("after_election", "")
    winner = "" if PLACEHOLDER.match(winner or "") else winner
    return {"candidates": cands, "winner": winner, "winner_party": fields.get("after_party", ""),
            "previous": fields.get("before_election", ""), "previous_party": fields.get("before_party", "")}


def party_code(p: str) -> str:
    p = (p or "").lower()
    if "democrat" in p:
        return "D"
    if "republican" in p:
        return "R"
    if "independent" in p:
        return "I"
    return "O" if p else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"\(.*?\)", "", (s or "").lower()))


def chamber_totals(pages: dict) -> dict:
    """Running seat counts from the national Senate and House articles (infobox seats_after)."""
    out = {}
    for office, title in (("Senate", "2026 United States Senate elections"), ("House", "2026 United States House of Representatives elections")):
        pg = pages.get(title)
        if not pg:
            continue
        m = re.search(r"\{\{\s*Infobox (?:legislative )?election(.*?)\n\}\}", pg["text"], flags=re.S | re.I)
        if not m:
            continue
        f = {}
        for line in re.split(r"\n\s*\|", m.group(1)):
            if "=" in line:
                k, v = line.split("=", 1)
                f[k.strip().lower()] = _clean(v)
        row = {}
        for i in range(1, 5):
            code = party_code(f.get(f"party{i}", ""))
            after = re.sub(r"[^\d]", "", f.get(f"seats_after{i}", "") or "")
            before = re.sub(r"[^\d]", "", f.get(f"seats_before{i}", "") or "")
            if code in ("D", "R", "I"):
                row[code] = {"after": int(after) if after else None, "before": int(before) if before else None}
        if row:
            out[office] = {"parties": row, "source": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}", "source_revised": pg["rev_time"]}
    return out


def fetch_wikitext(titles: list[str]) -> dict:
    import requests
    out = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        try:
            r = requests.get(API, params={"action": "query", "prop": "revisions", "rvprop": "content|timestamp", "rvslots": "main",
                                          "format": "json", "formatversion": 2, "redirects": 1, "titles": "|".join(chunk)},
                             headers=UA, timeout=30)
            r.raise_for_status()
            q = r.json().get("query", {})
        except Exception as e:
            print(f"  results fetch failed: {e}", file=sys.stderr)
            continue
        redirects = {x["to"]: x["from"] for x in q.get("redirects", [])}
        for pg in q.get("pages", []):
            t = redirects.get(pg.get("title"), pg.get("title"))
            if pg.get("missing"):
                out[t] = None
                continue
            rev = (pg.get("revisions") or [{}])[0]
            out[t] = {"text": rev.get("slots", {}).get("main", {}).get("content", ""), "rev_time": rev.get("timestamp", "")}
    return out


def update_results(offline: bool = False) -> dict:
    path = DATA / "results.json"
    prev = json.loads(path.read_text()) if path.exists() else {"races": {}}
    if offline:
        return prev
    races = []
    for fname, office, tfn in (("senate_2026.csv", "Senate", senate_title), ("governors_2026.csv", "Governor", gov_title)):
        for r in csv.DictReader((DATA / fname).open()):
            races.append((r["state"], office, tfn(r["state"])))
    pages = fetch_wikitext([t for _, _, t in races] + ["2026 United States Senate elections", "2026 United States House of Representatives elections"])
    if not pages:
        return prev
    out = {}
    found = 0
    for state, office, title in races:
        key = f"{state}|{office}"
        pg = pages.get(title)
        if not pg:
            if key in prev.get("races", {}):
                out[key] = prev["races"][key]
            continue
        ib = parse_infobox(pg["text"])
        if not ib:
            continue
        found += 1
        cands = ib["candidates"]
        for c in cands:
            c["party_code"] = party_code(c["party"])
        has_votes = any(c["votes"] for c in cands)
        if not has_votes:
            for c in cands:
                c["pct"] = None  # pre-election infobox numbers are not results
        w = ib["winner"]
        match = next((c for c in cands if w and (_norm(c["name"]) in _norm(w) or _norm(w) in _norm(c["name"]))), None)
        old = prev.get("races", {}).get(key, {})
        # Guard against premature or mistaken edits: a winner must match a listed candidate and
        # appear in two consecutive reads (about a minute apart) before we show it as called.
        seen_before = w and (old.get("winner") == w or old.get("pending_winner") == w)
        called = bool(match) and bool(seen_before)
        pending = w if (match and not called) else None
        out[key] = {
            "state": state, "office": office, "status": "called" if called else ("counting" if has_votes else "awaiting"),
            "winner": w if called else None,
            "winner_party": ((party_code(ib["winner_party"]) or match["party_code"]) if called else None),
            "pending_winner": pending,
            "candidates": cands, "source": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "source_revised": pg["rev_time"],
        }
    tally = {"Senate": {"D": 0, "R": 0, "I": 0, "uncalled": 0}, "Governor": {"D": 0, "R": 0, "I": 0, "uncalled": 0}}
    for v in out.values():
        t = tally[v["office"]]
        if v["status"] == "called" and v["winner_party"] in ("D", "R", "I"):
            t[v["winner_party"]] += 1
        else:
            t["uncalled"] += 1
    res = {"updated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "source_note": "Unofficial. Winners appear only after Wikipedia editors record a call by AP or major networks. Official results come from state certification.",
           "races_found": found, "tally": tally, "chambers": chamber_totals(pages) or prev.get("chambers", {}), "senate_not_up": {"D": 34, "R": 31}, "races": out}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, indent=1) + "\n")
    tmp.replace(path)  # atomic, so the site never reads a half-written file
    print(f"  results: {found} race pages parsed; Senate called D{tally['Senate']['D']} R{tally['Senate']['R']} I{tally['Senate']['I']}, uncalled {tally['Senate']['uncalled']}")
    return res


def results_window() -> bool:
    """True from 5 pm ET on Nov 3, 2026 (22:00 UTC) through the end of November."""
    now = dt.datetime.now(dt.timezone.utc)
    return dt.datetime(2026, 11, 3, 22, 0, tzinfo=dt.timezone.utc) <= now <= dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


if __name__ == "__main__":
    if "--auto" in sys.argv:
        # Called every minute by the live loop: every minute in the results window, else every 15th tick.
        tick = int(sys.argv[sys.argv.index("--tick") + 1]) if "--tick" in sys.argv else 0
        if results_window() or tick % 15 == 0:
            update_results()
        sys.exit(0)
    r = update_results()
    print("CHAMBERS:", json.dumps(r.get("chambers")))
    for k, v in sorted(r.get("races", {}).items())[:80]:
        print(k, v["status"], [c["name"] + "(" + c["party_code"] + ")" for c in v["candidates"]], v.get("winner"))
    missing = []
    for fname, office, tfn in (("senate_2026.csv", "Senate", senate_title), ("governors_2026.csv", "Governor", gov_title)):
        for row in csv.DictReader((DATA / fname).open()):
            if f"{row['state']}|{office}" not in r.get("races", {}):
                missing.append(tfn(row["state"]))
    print("MISSING:", missing)
