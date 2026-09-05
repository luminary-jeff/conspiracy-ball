#!/usr/bin/env python3
"""Season simulation CLI. Usage: python3 season_sim.py [--sims 20000] [--trade "Irving>Red9455:Smith"]"""
import argparse, warnings
warnings.filterwarnings("ignore")
from ff import config
from ff.season import SeasonContext
from ff.sim import run_sim, american

ap = argparse.ArgumentParser()
ap.add_argument("--sims", type=int, default=20000)
ap.add_argument("--trade", action="append", default=[], help='"MyPlayer>Owner:TheirPlayer" applied before simulating')
ap.add_argument("--seed", type=int, default=1)
args = ap.parse_args()
ctx = SeasonContext()
rows = run_sim(ctx, args.sims, args.seed, args.trade)
print("Projected regular season (weeks 1-%d), %d sims, optimal lineups every week%s" % (
    config.PLAYOFF_START_WEEK - 1, args.sims, "; trades: " + ", ".join(args.trade) if args.trade else ""))
print("%-4s %-20s %6s %8s %8s %6s %7s %7s %6s" % ("rank", "team", "wins", "pts/wk", "playoff", "bye", "title", "odds", "wk%d" % ctx.week))
for i, r in enumerate(sorted(rows, key=lambda x: -x["exp_wins"]), 1):
    me = "* " if r["roster_id"] == ctx.my_rid else ""
    print("%-4d %-20s %6.1f %8.1f %7.0f%% %5.0f%% %6.1f%% %7s %6.1f" % (
        i, (me + r["owner"])[:20], r["exp_wins"], r["pts_wk"], 100 * r["playoff"], 100 * r["bye"], 100 * r["title"], american(r["title"]), r["wk_proj"]))
