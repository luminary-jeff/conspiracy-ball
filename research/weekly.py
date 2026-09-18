"""Weekly update loop (Jeff's step H).  python3 research/weekly.py [--snapshot-only]

1. Snapshot, BEFORE Sunday kickoff when run then: this week's Sleeper projections and the tool's cached
   FantasyPros weekly pages, into research/snapshots/<season>_wk<NN>/. The FantasyPros copies build the
   weekly-rankings history nobody publishes; the projection copies let us keep verifying that Sleeper does
   not revise projections after the games.
2. Re-collect the current season, refit, re-evaluate.
3. Verify past snapshots against today's endpoint (the leak check) and print the 2026 scoreboard:
   raw RotoWire vs research-adjusted, week by week, each week predicted from earlier weeks only.
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402
from research.collect import pull  # noqa: E402
from research.evaluate import fit_ridge, predict  # noqa: E402
from ff.sleeper import Sleeper  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def snapshot(season, week):
    d = os.path.join(C.SNAP_DIR, "%s_wk%02d" % (season, week))
    os.makedirs(d, exist_ok=True)
    s = Sleeper(data_dir=d)
    url = "https://api.sleeper.app/projections/nfl/%s/%s" % (season, week)
    params = {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE", "K", "DEF"]}
    name = "projections_%s.json" % time.strftime("%Y%m%d_%H%M")
    data, _ = s.get_cached(url, name, 0, params=params, default=None)
    n = 0
    for f in glob.glob(os.path.join(C.ROOT, "data", "fp_wk_*.json")):
        shutil.copy2(f, os.path.join(d, "%s_%s" % (time.strftime("%Y%m%d", time.localtime(os.path.getmtime(f))),
                                                   os.path.basename(f))))
        n += 1
    print("snapshot: week %d projections %s, %d FantasyPros weekly files -> %s"
          % (week, "saved" if data else "FAILED", n, os.path.relpath(d, C.ROOT)))


def leak_check(season):
    """Compare every saved pre-game projection snapshot with what the endpoint serves now."""
    s = Sleeper(data_dir=C.RAW_DIR)
    for d in sorted(glob.glob(os.path.join(C.SNAP_DIR, "%s_wk*" % season))):
        week = int(d[-2:])
        snaps = sorted(glob.glob(os.path.join(d, "projections_*.json")))
        if not snaps:
            continue
        with open(snaps[-1]) as f:
            old = {r["player_id"]: (r.get("stats") or {}).get("pts_half_ppr") or 0.0 for r in json.load(f)["data"]}
        now = {r["player_id"]: (r.get("stats") or {}).get("pts_half_ppr") or 0.0
               for r in pull(s, "projections", season, week, 0)}
        both = [p for p in old if p in now and old[p] >= 5]      # the research pull has no kickers
        big = [p for p in both if abs(old[p] - now[p]) > 0.5]
        print("leak check week %d: %d of %d projected players differ by more than 0.5 from the snapshot %s"
              % (week, len(big), len(both), os.path.basename(snaps[-1])))


def scoreboard(season):
    rows = [r for r in F.build(F.load_rows()) if r["proj"] >= F.RELEVANT[r["pos"]]]
    with open(os.path.join(C.DATA_DIR, "model.json")) as f:
        lam = {p: m["lambda"] for p, m in json.load(f).items()}
    print("\n%d scoreboard: mean absolute error per player-game, each week predicted from earlier weeks only" % season)
    print("  week     n   RotoWire  adjusted   better?")
    tot = [0, 0.0, 0.0]
    for week in sorted(set(r["week"] for r in rows if r["season"] == season)):
        tw, eb, em = season * 100 + week, [], []
        for pos in C.POSITIONS:
            pr = [r for r in rows if r["pos"] == pos]
            t = np.array([r["t"] for r in pr])
            y = np.array([r["miss"] for r in pr])
            X = F.matrix(pr, F.ALL_FEATURES)
            m = fit_ridge(X[t < tw], y[t < tw], lam[pos])
            p = predict(m, X[t == tw])
            eb += list(np.abs(y[t == tw]))
            em += list(np.abs(y[t == tw] - p))
        print("  %4d  %4d   %7.3f   %7.3f   %s" % (week, len(eb), np.mean(eb), np.mean(em),
                                                   "adjusted" if np.mean(em) < np.mean(eb) else "RotoWire"))
        tot = [tot[0] + len(eb), tot[1] + sum(eb), tot[2] + sum(em)]
    if tot[0]:
        print("  all   %4d   %7.3f   %7.3f" % (tot[0], tot[1] / tot[0], tot[2] / tot[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-only", action="store_true")
    args = ap.parse_args()
    season = C.LIVE_SEASON
    week = int((Sleeper().state() or {}).get("week") or 1)
    snapshot(season, week)
    if args.snapshot_only:
        return
    for script in ("collect.py", "evaluate.py", "report.py"):
        extra = ["--refresh"] if script == "collect.py" else []
        r = subprocess.run([sys.executable, os.path.join(HERE, script)] + extra, capture_output=True, text=True)
        print("%s: %s" % (script, "ok" if r.returncode == 0 else "FAILED\n" + r.stderr[-800:]))
    leak_check(season)
    scoreboard(season)


if __name__ == "__main__":
    main()
