"""Build the player-game data set: pre-game RotoWire projection, actual, usage, and game context.

    python3 research/collect.py            # 2022-2025 all weeks + completed 2026 weeks
    python3 research/collect.py --refresh  # re-pull the current season's weeks and games.csv

One cached file per season-week under research/data/raw/ (past weeks never change, so they are fetched
once). About 150 Sleeper calls on a cold run, throttled to ~2/sec.
"""
import argparse
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from ff.sleeper import Sleeper  # noqa: E402
from ff.scoring import league_points  # noqa: E402

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FOREVER = 10 ** 12
USAGE = ["off_snp", "tm_off_snp", "pass_att", "rush_att", "rec_tgt", "rec", "rush_rz_att", "rec_rz_tgt",
         "rec_air_yd", "pass_yd", "rush_yd", "rec_yd"]
PROJ_PARTS = ["pass_att", "pass_yd", "pass_td", "rush_att", "rush_yd", "rush_td", "rec", "rec_yd", "rec_td"]
COLS = (["season", "week", "pid", "name", "pos", "team", "opp", "proj", "actual", "gp"]
        + ["a_" + k for k in USAGE] + ["p_" + k for k in PROJ_PARTS]
        + ["home", "spread", "total", "implied", "opp_implied", "roof", "temp", "wind", "rest", "opp_rest",
           "weekday", "div_game", "team_qb", "gameday"])
DEF_COLS = ["season", "week", "team", "opp", "fpa_QB", "fpa_RB", "fpa_WR", "fpa_TE", "pts_allow", "yds_allow"]


def fetch_games(refresh):
    if refresh or not os.path.exists(C.GAMES) or time.time() - os.path.getmtime(C.GAMES) > 24 * 3600:
        try:
            r = requests.get(GAMES_URL, timeout=60)
            r.raise_for_status()
            with open(C.GAMES, "w") as f:
                f.write(r.text)
        except Exception as e:
            print("[warn] games.csv fetch failed (%s); using the copy on disk" % e)
    out = {}
    for g in C.read_csv(C.GAMES):
        if g["game_type"] != "REG":
            continue
        season, week = int(g["season"]), int(g["week"])
        spread, total = C.fnum(g["spread_line"]), C.fnum(g["total_line"])  # spread > 0 = home favored
        for side, other, sign in (("home", "away", 1.0), ("away", "home", -1.0)):
            s = None if spread is None else sign * spread          # > 0 = this team favored
            imp = None if (s is None or total is None) else total / 2.0 + s / 2.0
            oimp = None if (s is None or total is None) else total / 2.0 - s / 2.0
            out[(season, week, g[side + "_team"])] = {
                "home": 1 if side == "home" and g["location"] == "Home" else 0,
                "spread": s, "total": total, "implied": imp, "opp_implied": oimp,
                "roof": g["roof"], "temp": C.fnum(g["temp"]), "wind": C.fnum(g["wind"]),
                "rest": C.fnum(g[side + "_rest"]), "opp_rest": C.fnum(g[other + "_rest"]),
                "weekday": g["weekday"], "div_game": g["div_game"], "team_qb": g[side + "_qb_name"],
                "gameday": g["gameday"], "played": g["home_score"] != "",
            }
    return out


def pull(s, kind, season, week, max_age):
    url = "https://api.sleeper.app/%s/nfl/%s/%s" % (kind, season, week)
    params = {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE", "DEF"]}
    before = s.calls
    data, _ = s.get_cached(url, "%s_%s_wk%02d.json" % (kind, season, week), max_age, params=params, default=[])
    if s.calls > before:
        time.sleep(0.5)
    return data or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-pull current-season weeks and games.csv")
    args = ap.parse_args()
    os.makedirs(C.RAW_DIR, exist_ok=True)
    scoring = C.load_scoring()
    if not scoring:
        sys.exit("no scoring settings available")
    games = fetch_games(args.refresh)
    s = Sleeper(data_dir=C.RAW_DIR)

    rows, drows, unmatched = [], [], set()
    for season in list(C.HIST_SEASONS) + [C.LIVE_SEASON]:
        for week in range(1, 19):
            wk_games = [v for (se, w, _), v in games.items() if se == season and w == week]
            if not wk_games or not all(g["played"] for g in wk_games):
                continue  # only fully completed weeks
            live = season == C.LIVE_SEASON
            age = 0 if (live and args.refresh) else FOREVER
            proj = pull(s, "projections", season, week, age)
            stats = pull(s, "stats", season, week, age)
            actual = {r["player_id"]: r for r in stats}
            for r in stats:
                if (r.get("player") or {}).get("position") == "DEF" or str(r.get("player_id", "")).isalpha():
                    st = r.get("stats") or {}
                    drows.append({"season": season, "week": week, "team": r.get("team"), "opp": r.get("opponent"),
                                  "fpa_QB": st.get("fan_pts_allow_qb"), "fpa_RB": st.get("fan_pts_allow_rb"),
                                  "fpa_WR": st.get("fan_pts_allow_wr"), "fpa_TE": st.get("fan_pts_allow_te"),
                                  "pts_allow": st.get("pts_allow"), "yds_allow": st.get("yds_allow")})
            for r in proj:
                pl = r.get("player") or {}
                pos = pl.get("position")
                if pos == "FB":
                    pos = "RB"
                if pos not in C.POSITIONS:
                    continue
                pst = r.get("stats") or {}
                p = league_points(pst, scoring, pos)
                if p < 1.0:
                    continue
                a = actual.get(r["player_id"])
                ast = (a or {}).get("stats") or {}
                team = r.get("team") or (a or {}).get("team")
                g = games.get((season, week, C.nv_team(team)))
                if g is None:
                    unmatched.add((season, week, team))
                    continue
                played = bool(a) and (ast.get("gp") or ast.get("gms_active") or ast.get("off_snp"))
                row = {"season": season, "week": week, "pid": r["player_id"],
                       "name": ("%s %s" % (pl.get("first_name", ""), pl.get("last_name", ""))).strip(),
                       "pos": pos, "team": team, "opp": r.get("opponent"), "proj": round(p, 2),
                       "actual": round(league_points(ast, scoring, pos), 2) if played else "",
                       "gp": 1 if played else 0}
                for k in USAGE:
                    row["a_" + k] = ast.get(k, "")
                for k in PROJ_PARTS:
                    row["p_" + k] = pst.get(k, "")
                row.update({k: ("" if v is None else v) for k, v in g.items() if k != "played"})
                rows.append(row)
        print("  %s: %d player-games so far (%d network calls)" % (season, len(rows), s.calls))

    C.write_csv(C.DATASET, rows, COLS)
    C.write_csv(C.DEFENSE, drows, DEF_COLS)
    print("\nwrote %s (%d rows) and %s (%d rows)" % (os.path.relpath(C.DATASET), len(rows),
                                                      os.path.relpath(C.DEFENSE), len(drows)))
    if unmatched:
        print("[warn] team-weeks with no games.csv match:", sorted(unmatched)[:12], "... %d total" % len(unmatched))
    print("\nplayed player-games with a projection >= 1 pt, by season and position:")
    print("  season " + "".join("%7s" % p for p in C.POSITIONS) + "   did-not-play")
    for season in sorted(set(r["season"] for r in rows)):
        sr = [r for r in rows if r["season"] == season]
        print("  %6s " % season + "".join("%7d" % sum(1 for r in sr if r["pos"] == p and r["gp"]) for p in C.POSITIONS)
              + "   %d" % sum(1 for r in sr if not r["gp"]))


if __name__ == "__main__":
    main()
