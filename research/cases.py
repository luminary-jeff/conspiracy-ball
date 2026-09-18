"""Case studies, read LAST and through the fitted model: Goff and Purdy, 2026.  python3 research/cases.py

For each game: RotoWire projection, research-adjusted projection, actual, how unusual the miss was against
four seasons of quarterbacks projected about the same, and what every pre-game feature contributed.
Then the decision itself: how often does the lower-projected of two close quarterbacks win, and by a lot?
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402
from research.apply import adjust, load_model, upcoming_rows  # noqa: E402
from research.collect import pull  # noqa: E402
from ff.sleeper import Sleeper  # noqa: E402
from ff.scoring import league_points  # noqa: E402

NAMES = ("Jared Goff", "Brock Purdy")


def main():
    season = C.LIVE_SEASON
    hist = F.load_rows()
    done_weeks = sorted(set(r["week"] for r in hist if r["season"] == season))
    cur = (done_weeks[-1] + 1) if done_weeks else 1
    pend = upcoming_rows(season, cur)
    # a finished game inside an unfinished week (Thursday night): take its box score
    s = Sleeper(data_dir=C.RAW_DIR)
    scoring = C.load_scoring()
    live = {r["player_id"]: r for r in pull(s, "stats", season, cur, 1800)}
    for r in pend:
        st = (live.get(r["pid"]) or {}).get("stats") or {}
        if st.get("gp") or st.get("off_snp"):
            r["actual"] = round(league_points(st, scoring, r["pos"]), 2)
    rows = F.build(sorted(hist + pend, key=lambda r: (r["t"], r["pid"])))
    model = load_model()
    qbs = [r for r in hist if r["pos"] == "QB" and r["proj"] >= F.RELEVANT["QB"]]
    out = {"games": [], "pairs": {}}

    print("CASE STUDIES (2026)\n")
    for r in rows:
        if r["season"] != season or r["name"] not in NAMES:
            continue
        adj, contrib = adjust(r, model)
        peers = np.array([q["actual"] for q in qbs if abs(q["proj"] - r["proj"]) <= 2.0])
        print("%s, week %d vs %s: RotoWire %.1f, adjusted %.1f, actual %s" % (
            r["name"], r["week"], r["opp"], r["proj"], adj, "pending" if r["actual"] is None else "%.1f" % r["actual"]))
        g = {"name": r["name"], "week": r["week"], "opp": r["opp"], "proj": r["proj"], "adj": adj, "actual": r["actual"],
             "contrib": contrib, "peers": int(len(peers))}
        if r["actual"] is not None:
            miss = r["actual"] - r["proj"]
            sd = float((peers - np.array([q["proj"] for q in qbs if abs(q["proj"] - r["proj"]) <= 2.0])).std())
            tail = float((peers >= r["actual"]).mean()) if miss > 0 else float((peers <= r["actual"]).mean())
            g.update({"miss": miss, "z": miss / sd, "tail": tail, "explained": adj - r["proj"]})
            print("   miss %+.1f = %.1f standard deviations. Of %d quarterbacks projected within 2 points of him since 2022, "
                  "%.0f%% scored at least this %s." % (miss, miss / sd, len(peers), 100 * tail, "high" if miss > 0 else "low"))
            print("   the pre-game features moved the projection %+.2f, against a miss of %+.1f: they explain %.0f%% of it."
                  % (adj - r["proj"], miss, 100 * max(0.0, (adj - r["proj"]) / miss) if miss else 0))
        print("   contributions (points): " + ", ".join("%s %+.2f" % kv for kv in sorted(contrib.items(), key=lambda kv: -abs(kv[1]))[:6]))
        print("   line: spread %+.1f, total %.1f, implied %.1f, %s" % (r["spread"] or 0, r["total"] or 0, r["implied"] or 0, r["roof"]))
        print()
        out["games"].append(g)

    print("THE DECISION: two quarterbacks, close projections, 2022-2026")
    by_week = {}
    for q in qbs:
        if q["proj"] >= F.STARTABLE["QB"]:
            by_week.setdefault(q["t"], []).append(q)
    for lo, hi in ((0.0, 1.0), (1.0, 2.5), (2.5, 4.5)):
        n = wrong = wrong10 = 0
        for g in by_week.values():
            for i in range(len(g)):
                for j in range(i + 1, len(g)):
                    a, b = (g[i], g[j]) if g[i]["proj"] >= g[j]["proj"] else (g[j], g[i])
                    gap = a["proj"] - b["proj"]
                    if lo <= gap < hi:
                        n += 1
                        wrong += b["actual"] > a["actual"]
                        wrong10 += (b["actual"] - a["actual"]) >= 10
        out["pairs"]["%.1f-%.1f" % (lo, hi)] = {"n": n, "wrong": wrong / float(n), "wrong10": wrong10 / float(n)}
        print("  projection gap %.1f to %.1f: the lower-projected QB outscores the other %.0f%% of the time, "
              "and by 10+ points %.0f%% of the time  (n=%d pairs)" % (lo, hi, 100.0 * wrong / n, 100.0 * wrong10 / n, n))
    with open(os.path.join(C.DATA_DIR, "cases.json"), "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
