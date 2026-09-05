#!/usr/bin/env python3
"""Conspiracy Ball assistant CLI.

  python3 ff.py draft-prep                 # build the value board (run the night before)
  python3 ff.py draft-live                 # live draft screen (polls Sleeper every 5s)
  python3 ff.py draft-live --dry-run       # ~2-minute simulated draft to test the display
  python3 ff.py brief                      # in-season: the right sections for today (Tue: recap/waivers/trades; game days: lineup/preview)
  python3 ff.py lineup | preview | waivers | trades | recap
  python3 ff.py trade give Waddle for Nabers
"""
import argparse
import sys
import time
import warnings

warnings.filterwarnings("ignore")  # urllib3 LibreSSL notice on macOS system Python

from ff import config
from ff.sleeper import Sleeper, bye_weeks
from ff.sources import fetch_fantasypros, load_manual_csv
from ff.board import build_board, write_board, load_board
from ff.util import age_str, warn


def _fmt_start(ms):
    if not ms:
        return "not set"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromtimestamp(ms / 1000, ZoneInfo("America/New_York"))
        return "%s ET (%s local)" % (dt.strftime("%a %b %-d %-I:%M %p"), time.strftime("%H:%M %Z", time.localtime(ms / 1000)))
    except Exception:
        return time.strftime("%a %H:%M local", time.localtime(ms / 1000))


def cmd_draft_prep(args):
    api = Sleeper()
    t0 = time.time()
    print("Conspiracy Ball — draft prep")
    league = api.league(force=args.force)
    if not league.get("scoring_settings"):
        print("!! could not load league scoring (offline?). Aborting."); return 2
    sc = league["scoring_settings"]
    print("  league: %s | %s teams | rec=%s pass_yd=%s pass_td=%s int=%s fum_lost=%s" % (
        league.get("name"), league.get("total_rosters"), sc.get("rec"), sc.get("pass_yd"), sc.get("pass_td"),
        sc.get("pass_int"), sc.get("fum_lost")))

    players, pts = api.players()
    print("  players map: %d players (fetched %s)" % (len(players or {}), age_str(pts)))
    if not players:
        print("!! no player map available. Aborting."); return 2

    proj, pts_proj = api.projections_season(force=args.force)
    n_proj = sum(1 for p in proj or [] if (p.get('stats') or {}).get('pts_half_ppr'))
    print("  sleeper projections: %d rows, %d with season points (fetched %s)" % (len(proj or []), n_proj, age_str(pts_proj)))

    fp, pts_fp = fetch_fantasypros("half", force=args.force)
    print("  fantasypros half-PPR ECR: %d players (fetched %s)" % (len(fp), age_str(pts_fp)))

    sched, _ = api.schedule()
    byes = bye_weeks(sched)
    print("  bye weeks: %d teams from schedule" % len(byes))

    manual = load_manual_csv()
    if manual:
        print("  manual_rankings.csv: %d overrides" % len(manual))

    rows = build_board(league, players, proj, fp, byes, manual)
    meta = {"sources": {"sleeper_projections": age_str(pts_proj), "fantasypros_ecr": age_str(pts_fp),
                        "players": age_str(pts)},
            "n_projected": n_proj, "n_ecr": len(fp)}
    path = write_board(rows, meta)
    print("  board: %d players -> %s (+ board.txt)" % (len(rows), path))

    # draft status
    draft = api.draft(live=True)
    order = draft.get("draft_order") or {}
    slot = order.get(config.MY_USER_ID)
    ds = draft.get("settings") or {}
    print("  draft: status=%s  type=%s  rounds=%s  timer=%ss  order=%s  start=%s" % (
        draft.get("status"), draft.get("type"), ds.get("rounds"), ds.get("pick_timer"),
        "SET (my slot %s)" % slot if slot else "not set yet",
        _fmt_start(draft.get("start_time"))))
    if slot:
        from ff.draft import slot_pick_numbers
        print("  my picks: %s" % ", ".join("#%d" % n for n in slot_pick_numbers(slot, ds.get("teams") or 12, ds.get("rounds") or 15)))
    keepers = [p for p in (api.draft_picks() or []) if p.get("is_keeper")]
    if keepers:
        print("  keepers already on board: %d" % len(keepers))

    print("\nTop 24 by value:")
    from ff.board import fmt_row
    for r in rows[:24]:
        print("  " + fmt_row(r))
    print("\nTier summary (players in tier 1 / 2 / 3):")
    for pos in config.POSITIONS:
        g = [r for r in rows if r["pos"] == pos]
        counts = [sum(1 for r in g if r["tier"] == t) for t in (1, 2, 3)]
        print("  %-3s %s" % (pos, " / ".join(str(c) for c in counts)))
    print("\ndone in %.1fs, %d API calls. Full board: data/board.txt" % (time.time() - t0, api.calls))
    return 0


def cmd_draft_live(args):
    from ff.draft import run_live, run_dry_run
    board = load_board()
    if not board:
        print("No board found. Run: python3 ff.py draft-prep"); return 2
    age = time.time() - board.get("built_at", 0)
    if age > 36 * 3600:
        warn("board is %s old — consider re-running draft-prep" % age_str(board.get("built_at")))
    if args.dry_run:
        return run_dry_run(board, slot=args.slot, speed=args.speed, my_pause=args.pause)
    return run_live(board, slot=args.slot, interval=args.interval, once=args.once)


def cmd_season(args):
    from ff.brief import render_brief, render_trade_eval
    from ff.season import SeasonContext
    ctx = SeasonContext(week=args.week)
    if args.cmd == "trade" and args.text:
        print(render_trade_eval(ctx, " ".join(args.text)))
        return 0
    sections = None if args.cmd == "brief" else [args.cmd if args.cmd != "trade" else "trades"]
    if args.cmd == "brief" and args.all:
        sections = ["lineup", "preview", "waivers", "trades", "recap"]
    print(render_brief(ctx, sections))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ff.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("draft-prep", help="build the value board")
    p.add_argument("--force", action="store_true", help="ignore caches and refetch everything")
    p.set_defaults(fn=cmd_draft_prep)
    p = sub.add_parser("draft-live", help="live draft screen")
    p.add_argument("--dry-run", action="store_true", help="simulate a full draft against ADP")
    p.add_argument("--slot", type=int, help="override my draft slot (1-12)")
    p.add_argument("--interval", type=float, default=5.0, help="poll seconds (live)")
    p.add_argument("--speed", type=float, default=0.35, help="seconds per simulated pick (dry run)")
    p.add_argument("--pause", type=float, default=3.0, help="seconds to hold on my simulated picks")
    p.add_argument("--once", action="store_true", help="render one frame and exit")
    p.set_defaults(fn=cmd_draft_live)
    for name, help_ in (("brief", "day-aware in-season brief (lineup/preview/waivers/trades/recap)"),
                        ("lineup", "optimal starters + moves to make"), ("preview", "this week's matchup + win probability"),
                        ("waivers", "ranked claims with FAAB bids and drops"), ("trades", "trade offers worth sending; or evaluate: trade give X for Y"),
                        ("recap", "last week's results, standings, trash talk")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--week", type=int, help="override NFL week")
        if name == "brief":
            p.add_argument("--all", action="store_true", help="every section")
        if name == "trade" or name == "trades":
            p.add_argument("text", nargs="*", help="e.g. give Waddle for Nabers")
        p.set_defaults(fn=cmd_season)
    sub.add_parser("trade", help="evaluate a trade: trade give X for Y").set_defaults(fn=cmd_season)
    sub.choices["trade"].add_argument("text", nargs="*")
    sub.choices["trade"].add_argument("--week", type=int)
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help(); return 1
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
