"""Season Monte Carlo: optimal lineups every week for every team, actual results for
played weeks, playoff bracket (6 teams, weeks 15-17). Returns per-team odds."""
import math
import random
import sys
from collections import defaultdict

from . import config
from .season import optimal_lineup, TEAM_SD, INJ_MULT
from .scoring import league_points
from .util import norm_name

LONG_OUT = {"IR", "PUP", "NA", "Sus"}


def apply_trades(ctx, trades):
    """trades: ['MyPlayer>Owner:TheirPlayer', ...] applied to ctx rosters in memory."""
    for t in trades or []:
        give, rest = t.split(">")
        owner, get = rest.split(":")

        def pid_of(n):
            k = norm_name(n)
            return next(p for p in ctx.owned if norm_name(ctx.name(p)) == k or
                        norm_name((ctx.players.get(p) or {}).get("last_name") or "") == k)
        g, r = pid_of(give), pid_of(get)
        other = next(x for x in ctx.rosters if ctx.users.get(x.get("owner_id")) == owner)
        me = ctx.by_roster[ctx.my_rid]
        me["players"] = [p for p in me["players"] if p != g] + [r]
        other["players"] = [p for p in other["players"] if p != r] + [g]
        ctx.owned[g], ctx.owned[r] = other["roster_id"], ctx.my_rid


def run_sim(ctx, sims=20000, seed=1, trades=None):
    apply_trades(ctx, trades)
    api = ctx.api
    last_reg = config.PLAYOFF_START_WEEK - 1
    weeks = list(range(1, 18))
    wk_proj = {}
    for w in weeks:
        proj, _ = api.projections_week(w, ctx.season)
        d = {}
        for p in proj or []:
            pid = str(p.get("player_id"))
            pos = ((p.get("player") or {}).get("position")) or (ctx.players.get(pid) or {}).get("position")
            if pos == "FB":
                pos = "RB"
            d[pid] = league_points(p.get("stats") or {}, ctx.scoring, pos)
        wk_proj[w] = d

    def lines_for(rid, w):
        out = []
        for pid in ctx.by_roster[rid].get("players") or []:
            p = ctx.players.get(pid) or {}
            pos = p.get("position")
            pos = "RB" if pos == "FB" else pos
            proj = wk_proj[w].get(pid, 0.0)
            if ctx.byes.get(p.get("team")) == w:
                proj = 0.0
            inj = p.get("injury_status")
            if inj in LONG_OUT:
                proj = 0.0
            elif w == ctx.week:
                proj *= INJ_MULT.get(inj, 1.0)
            out.append({"pid": pid, "pos": pos, "eff": proj, "fp_std": 0.0})
        return out

    rids = sorted(ctx.by_roster)
    team_proj = {rid: {w: optimal_lineup(lines_for(rid, w))[1] for w in weeks} for rid in rids}
    sched, actual = {}, {}
    for w in range(1, last_reg + 1):
        ms = api.matchups(w) or []
        by = defaultdict(list)
        for m in ms:
            by[m.get("matchup_id")].append(m["roster_id"])
        sched[w] = [tuple(v) for v in by.values() if len(v) == 2]
        if w < ctx.week and ms and any((m.get("points") or 0) > 0 for m in ms):
            actual[w] = {m["roster_id"]: m.get("points") or 0 for m in ms}

    rng = random.Random(seed)
    wins_tot, pts_tot = defaultdict(float), defaultdict(float)
    playoffs, byes, titles, seeds = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(lambda: defaultdict(int))
    for _ in range(sims):
        wins, pts = defaultdict(int), defaultdict(float)
        for w in range(1, last_reg + 1):
            sc = actual.get(w) or {rid: rng.gauss(team_proj[rid][w], TEAM_SD) for rid in rids}
            for a, b in sched.get(w, []):
                pts[a] += sc[a]
                pts[b] += sc[b]
                if sc[a] > sc[b]:
                    wins[a] += 1
                else:
                    wins[b] += 1
        order = sorted(rids, key=lambda r: (-wins[r], -pts[r]))
        top6 = order[:6]
        for i, r in enumerate(top6):
            playoffs[r] += 1
            seeds[r][i + 1] += 1
        for r in top6[:2]:
            byes[r] += 1

        def game(a, b, w):
            return a if rng.gauss(team_proj[a][w], TEAM_SD) > rng.gauss(team_proj[b][w], TEAM_SD) else b
        s1, s2, s3, s4, s5, s6 = top6
        w36, w45 = game(s3, s6, 15), game(s4, s5, 15)
        lo = w36 if top6.index(w36) > top6.index(w45) else w45
        hi = w45 if lo == w36 else w36
        titles[game(game(s1, lo, 16), game(s2, hi, 16), 17)] += 1
        for r in rids:
            wins_tot[r] += wins[r]
            pts_tot[r] += pts[r]
    rows = []
    for r in rids:
        rows.append({"roster_id": r, "owner": ctx.owner_name(r), "exp_wins": round(wins_tot[r] / sims, 2),
                     "pts_wk": round(pts_tot[r] / sims / last_reg, 1), "playoff": playoffs[r] / sims,
                     "bye": byes[r] / sims, "title": titles[r] / sims, "wk_proj": round(team_proj[r][ctx.week], 1),
                     "played": len(actual)})
    rows.sort(key=lambda x: -x["title"])
    return rows


def american(p, vig=0.0):
    """Probability -> American odds string (optionally shaded by a bookmaker's vig)."""
    p = min(max(p * (1 + vig), 0.002), 0.995)
    if p >= 0.5:
        return "-%d" % round(100 * p / (1 - p))
    return "+%d" % round(100 * (1 - p) / p)
