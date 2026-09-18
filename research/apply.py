"""Apply the fitted adjustment to a week that has not finished (Jeff's step G).

    python3 research/apply.py            # current week, my roster and my opponent's
    python3 research/apply.py --week 3

Builds pre-game feature rows for the week from the live projections endpoint and the current betting line,
then prints RotoWire vs adjusted for each player. Reads model.json written by evaluate.py.
Research output only; the tool's recommendations do not use it.
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402
from research.collect import fetch_games, pull, PROJ_PARTS, USAGE  # noqa: E402
from ff.sleeper import Sleeper  # noqa: E402
from ff.scoring import league_points  # noqa: E402
from ff import config  # noqa: E402


def upcoming_rows(season, week, refresh_lines=True):
    """Feature-ready pseudo-rows (no actuals) for every projected skill player in an unfinished week."""
    scoring = C.load_scoring()
    games = fetch_games(refresh_lines)
    s = Sleeper(data_dir=C.RAW_DIR)
    proj = pull(s, "projections", season, week, 3 * 3600)
    out = []
    for r in proj:
        pl = r.get("player") or {}
        pos = "RB" if pl.get("position") == "FB" else pl.get("position")
        if pos not in C.POSITIONS:
            continue
        pst = r.get("stats") or {}
        p = league_points(pst, scoring, pos)
        g = games.get((season, week, C.nv_team(r.get("team"))))
        if p < F.RELEVANT[pos] or g is None:
            continue
        row = {"season": season, "week": week, "t": season * 100 + week, "pid": r["player_id"],
               "name": ("%s %s" % (pl.get("first_name", ""), pl.get("last_name", ""))).strip(), "pos": pos,
               "team": r.get("team"), "opp": r.get("opponent"), "proj": round(p, 2), "actual": None, "miss": 0.0,
               "pending": True}
        for k in USAGE:
            row["a_" + k] = None
        for k in PROJ_PARTS:
            row["p_" + k] = pst.get(k)
        row.update({k: v for k, v in g.items() if k != "played"})
        out.append(row)
    return out


def load_model():
    with open(os.path.join(C.DATA_DIR, "model.json")) as f:
        m = json.load(f)
    for pos in m:
        for k in ("mu", "sd", "beta"):
            m[pos][k] = np.array(m[pos][k])
    return m


def adjust(row, model):
    """(adjusted projection, {feature: contribution in points}) for one feature-built row."""
    m = model[row["pos"]]
    x = np.array([np.nan if row["x"].get(f) is None else row["x"][f] for f in m["features"]], dtype=float)
    z = np.nan_to_num((x - m["mu"]) / m["sd"])
    contrib = dict(zip(m["features"], (z * m["beta"]).tolist()))
    contrib["bias"] = m["b0"]
    return row["proj"] + m["b0"] + float(z @ m["beta"]) if len(z) else row["proj"], contrib


def adjusted_week(season, week):
    hist = [r for r in F.load_rows() if r["t"] < season * 100 + week]
    pend = upcoming_rows(season, week)
    rows = F.build(sorted(hist + pend, key=lambda r: (r["t"], r["pid"])))
    model = load_model()
    out = []
    for r in rows:
        if r.get("pending"):
            r["adj"], r["contrib"] = adjust(r, model)
            out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int)
    args = ap.parse_args()
    s = Sleeper()
    week = args.week or int((s.state() or {}).get("week") or 1)
    season = int(config.SEASON)
    by_pid = {r["pid"]: r for r in adjusted_week(season, week)}
    rosters = {r["roster_id"]: r for r in (s.rosters() or [])}
    mine = rosters.get(config.MY_ROSTER_ID) or {}
    opp_rid = None
    for m in s.matchups(week) or []:
        if m["roster_id"] == config.MY_ROSTER_ID:
            opp_rid = next((o["roster_id"] for o in s.matchups(week) if o["matchup_id"] == m["matchup_id"]
                            and o["roster_id"] != config.MY_ROSTER_ID), None)
    print("Week %d, RotoWire vs research-adjusted projection (research output, not used by the tool)\n" % week)
    for label, ros in (("MY ROSTER", mine), ("OPPONENT", rosters.get(opp_rid) or {})):
        print(label)
        rs = [by_pid[p] for p in (ros.get("players") or []) if p in by_pid]
        for r in sorted(rs, key=lambda r: (C.POSITIONS.index(r["pos"]), -r["proj"])):
            top = sorted(((k, v) for k, v in r["contrib"].items()), key=lambda kv: -abs(kv[1]))[:2]
            print("  %-3s %-22s RotoWire %5.1f  adjusted %5.1f  (%+.1f: %s)" % (
                r["pos"], r["name"][:22], r["proj"], r["adj"], r["adj"] - r["proj"],
                ", ".join("%s %+.2f" % kv for kv in top)))
        print()


if __name__ == "__main__":
    main()
