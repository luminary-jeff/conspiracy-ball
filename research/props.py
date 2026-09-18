"""Player prop lines (DraftKings, served by ESPN's core API) as a tenth hypothesis.

    python3 research/props.py --snapshot [--week N]   # save this week's and last week's lines
    python3 research/props.py --evaluate              # RotoWire vs prop-swapped projection on finished weeks

What the feed has: over/under LINES for passing, rushing and receiving yards, receptions, carries, attempts.
What it lacks: prices. "Anytime touchdown" entries carry no odds, so touchdowns cannot be read from it.
The prop-swapped projection therefore keeps RotoWire's touchdowns, interceptions and fumbles and replaces
only the yardage and catch components with the market's numbers.
ESPN keeps lines for finished games of the current season (week 1 was still served on 2026-09-18) but had
purged 2025, so this can only be tested forward. Snapshot early in the season, judge around week 8.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402

UA = {"User-Agent": "curl/8.4.0"}     # ESPN rejects browser-like and custom agents
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
ATHLETES = os.path.join(C.DATA_DIR, "espn_athletes.json")
# prop type name -> the RotoWire stat it replaces
TYPES = {"Total Passing Yards (incl. overtime)": "pass_yd", "Total Rushing Yards (incl. overtime)": "rush_yd",
         "Total Receiving Yards (incl. overtime)": "rec_yd", "Total Receptions (incl. overtime)": "rec"}


def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[warn] %s failed: %s" % (url.split("/nfl")[-1][:60], e))
        return None


def norm(name):
    n = re.sub(r"[^a-z ]", "", (name or "").lower())
    return " ".join(w for w in n.split() if w not in ("jr", "sr", "ii", "iii", "iv", "v"))


def athlete_map(max_age=7 * 24 * 3600):
    """{espn athlete id: {"name", "pos", "team"}}, merged over time so traded/cut players stay resolvable."""
    known = {}
    if os.path.exists(ATHLETES):
        with open(ATHLETES) as f:
            known = json.load(f)
        if time.time() - os.path.getmtime(ATHLETES) < max_age:
            return known
    teams = _get(SITE + "/teams", {"limit": 40}) or {}
    for t in (((teams.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []):
        team = t["team"]
        roster = _get("%s/teams/%s/roster" % (SITE, team["id"])) or {}
        for grp in roster.get("athletes") or []:
            for a in grp.get("items") or []:
                known[str(a["id"])] = {"name": a.get("fullName"), "pos": (a.get("position") or {}).get("abbreviation"),
                                       "team": team.get("abbreviation")}
        time.sleep(0.3)
    if known:
        with open(ATHLETES, "w") as f:
            json.dump(known, f)
    return known


def snapshot(season, week):
    """Save every player line for every game of the week. One call per game."""
    sb = _get(SITE + "/scoreboard", {"week": week, "seasontype": 2, "dates": season}) or {}
    events, n = {}, 0
    for ev in sb.get("events") or []:
        eid, items, page = ev["id"], [], 1
        while True:
            d = _get("%s/events/%s/competitions/%s/odds/100/propBets" % (CORE, eid, eid), {"limit": 1000, "page": page})
            if not d:
                break
            for it in d.get("items") or []:
                ref = (it.get("athlete") or {}).get("$ref") or ""
                m = re.search(r"/athletes/(\d+)", ref)
                cur = ((it.get("current") or {}).get("target") or {}).get("value")
                if m and cur is not None and it.get("type", {}).get("name") in TYPES:
                    items.append({"a": m.group(1), "type": it["type"]["name"], "line": cur,
                                  "open": ((it.get("open") or {}).get("target") or {}).get("value"),
                                  "updated": it.get("lastUpdated")})
            if page >= (d.get("pageCount") or 1):
                break
            page += 1
        events[eid] = {"name": ev.get("shortName"), "kickoff": ev.get("date"), "items": items}
        n += len(items)
        time.sleep(0.3)
    if not n:
        print("props: week %d, nothing served" % week)
        return
    d = os.path.join(C.SNAP_DIR, "%s_wk%02d" % (season, week))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "props_%s.json" % time.strftime("%Y%m%d_%H%M"))
    with open(path, "w") as f:
        json.dump({"fetched_at": time.time(), "season": season, "week": week, "events": events}, f)
    print("props: week %d, %d lines across %d games -> %s" % (week, n, len(events), os.path.relpath(path, C.ROOT)))


def lines_for(season, week):
    """{normalized name: {stat: line, "_src": {stat: "pregame"|"open"}}}.

    For a finished game ESPN's "current" value is the last LIVE in-game line, not the close (710 of week 1's
    1,058 lines had been touched after kickoff when first captured). So, per player and stat: take the newest
    snapshot value that was last updated before kickoff; if every snapshot is post-kickoff, fall back to the
    OPENING line, which is always pre-game but days older. Pre-kickoff snapshots (Thu and Sun runs) are what
    make this sharp."""
    snaps = sorted(glob.glob(os.path.join(C.SNAP_DIR, "%s_wk%02d" % (season, week), "props_*.json")))
    ath = athlete_map() if snaps else {}
    out = {}
    for path in snaps:                                   # oldest first, later pre-game values overwrite
        with open(path) as f:
            snap = json.load(f)
        for ev in snap["events"].values():
            for it in ev["items"]:
                a = ath.get(it["a"])
                if not a:
                    continue
                rec = out.setdefault(norm(a["name"]), {"_src": {}})
                stat = TYPES[it["type"]]
                pregame = not (it.get("updated") and ev.get("kickoff") and it["updated"] > ev["kickoff"])
                if pregame:
                    rec[stat], rec["_src"][stat] = it["line"], "pregame"
                elif it.get("open") is not None and rec["_src"].get(stat) != "pregame":
                    rec[stat], rec["_src"][stat] = it["open"], "open"
    return out


def swapped(row, lines, scoring):
    """RotoWire projection with yardage/catch components replaced by the market's lines; None if no line."""
    mine = lines.get(norm(row["name"]))
    if not mine:
        return None
    adj, used = row["proj"], 0
    for stat, line in mine.items():
        if stat == "_src":
            continue
        rw = C.fnum(row.get("p_" + stat))
        if rw is None or not scoring.get(stat):
            continue
        adj += float(scoring[stat]) * (line - rw)
        used += 1
    return adj if used else None


def evaluate(verbose=True):
    scoring = C.load_scoring()
    rows = [r for r in F.load_rows() if r["season"] == C.LIVE_SEASON and r["proj"] >= F.RELEVANT[r["pos"]]]
    res, tot = [], [0, 0.0, 0.0, 0.0, 0.0]
    for week in sorted(set(r["week"] for r in rows)):
        lines = lines_for(C.LIVE_SEASON, week)
        eb, em = [], []
        for r in rows:
            if r["week"] != week:
                continue
            adj = swapped(r, lines, scoring)
            if adj is not None:
                eb.append(r["actual"] - r["proj"])
                em.append(r["actual"] - adj)
        if eb:
            eb, em = np.array(eb), np.array(em)
            res.append({"week": week, "n": len(eb), "mae_rw": float(np.abs(eb).mean()), "mae_prop": float(np.abs(em).mean()),
                        "bias_rw": float(eb.mean()), "bias_prop": float(em.mean())})
            tot = [tot[0] + len(eb), tot[1] + np.abs(eb).sum(), tot[2] + np.abs(em).sum(), tot[3] + eb.sum(), tot[4] + em.sum()]
    if verbose:
        print("\nH10 prop lines: RotoWire vs prop-swapped projection, players with at least one line")
        print("  (no fitting: the market's yardage and catch lines simply replace RotoWire's; lines are medians, so a")
        print("   negative bias is expected and says nothing by itself. Judge on MAE, and not before ~1,500 games.)")
        print("  week     n   MAE RotoWire  MAE props   bias RotoWire  bias props")
        for r in res:
            print("  %4d  %4d   %10.3f  %9.3f   %+12.2f  %+10.2f" % (r["week"], r["n"], r["mae_rw"], r["mae_prop"], r["bias_rw"], r["bias_prop"]))
        if tot[0]:
            print("  all   %4d   %10.3f  %9.3f   %+12.2f  %+10.2f" % (tot[0], tot[1] / tot[0], tot[2] / tot[0], tot[3] / tot[0], tot[4] / tot[0]))
        else:
            print("  no finished week has a snapshot yet")
    with open(os.path.join(C.DATA_DIR, "props_results.json"), "w") as f:
        json.dump({"weeks": res, "n": tot[0], "mae_rw": tot[1] / tot[0] if tot[0] else None,
                   "mae_prop": tot[2] / tot[0] if tot[0] else None}, f, indent=1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--week", type=int)
    args = ap.parse_args()
    if args.snapshot:
        from ff.sleeper import Sleeper
        week = args.week or int((Sleeper().state() or {}).get("week") or 1)
        for w in ([week] if args.week else [w for w in (week - 1, week) if w >= 1]):
            snapshot(C.LIVE_SEASON, w)
    if args.evaluate or not args.snapshot:
        evaluate()


if __name__ == "__main__":
    main()
