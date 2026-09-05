"""In-season data assembly and analysis: lineup, preview, waivers, trades, recap.

All numbers are league-scored. Every source is optional; missing sources degrade
to fewer signals, never to a crash.
"""
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

from . import config
from .board import _player_index, _match_fp, build_board
from .draft import lineup_value
from .scoring import league_points
from .sleeper import Sleeper, bye_weeks
from .sources import fetch_fantasypros, FP_HEADERS
from .util import norm_team, warn

SLOT_ORDER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
INJ_MULT = {"Out": 0.0, "IR": 0.0, "PUP": 0.0, "Sus": 0.0, "NA": 0.0, "Doubtful": 0.3, "Questionable": 0.9}
TEAM_SD = 20.0          # weekly team score standard deviation (points), for win probability


# ------------------------------------------------------------------ extra sources
def fetch_fp_page(slug, name, max_age=3 * 3600):
    """Generic FantasyPros ecrData page fetch with cache. Returns (players, meta)."""
    path = os.path.join(config.DATA_DIR, name)
    cached = None
    if os.path.exists(path):
        try:
            blob = json.load(open(path))
            cached = blob
            if time.time() - blob.get("fetched_at", 0) < max_age:
                return blob["data"], blob.get("meta", {})
        except Exception:
            pass
    try:
        r = requests.get("https://www.fantasypros.com/nfl/rankings/%s" % slug, headers=FP_HEADERS, timeout=25)
        r.raise_for_status()
        m = re.search(r"var ecrData = (\{.*?\});\s*\n", r.text, re.S)
        if not m:
            raise ValueError("no ecrData")
        d = json.loads(m.group(1))
        players = d.get("players") or []
        if not players:
            raise ValueError("empty")
        meta = {"week": d.get("week"), "type": d.get("type"), "last_updated": d.get("last_updated")}
        json.dump({"fetched_at": time.time(), "data": players, "meta": meta}, open(path, "w"))
        return players, meta
    except Exception as e:
        if cached:
            warn("FantasyPros %s failed (%s); using cache" % (slug, e))
            return cached["data"], cached.get("meta", {})
        warn("FantasyPros %s unavailable (%s)" % (slug, e))
        return [], {}


def fetch_espn_odds(week, season=config.SEASON):
    """ESPN scoreboard: kickoff times, spreads, totals -> {team: {...}}. Undocumented, optional."""
    api = Sleeper()
    api.session.headers.update({"User-Agent": "curl/8.4.0"})   # ESPN 403s browser-like AND custom UAs; curl's passes
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    data, _ = api.get_cached(url, "espn_wk%s.json" % week, 2 * 3600,
                             params={"week": week, "seasontype": 2, "dates": season}, default={})
    out = {}
    for ev in (data or {}).get("events", []):
        try:
            comp = ev["competitions"][0]
            kick = datetime.strptime(ev["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            odds = (comp.get("odds") or [{}])[0]
            total = odds.get("overUnder")
            spread_txt = odds.get("details") or ""
            teams = {t["homeAway"]: norm_team(t["team"]["abbreviation"]) for t in comp["competitors"]}
            fav, line = None, None
            m = re.match(r"([A-Z]+) (-?[\d.]+)", spread_txt)
            if m:
                fav, line = norm_team(m.group(1)), float(m.group(2))
            for side, team in teams.items():
                opp = teams["away" if side == "home" else "home"]
                implied = None
                if total is not None and line is not None:
                    implied = total / 2 + (abs(line) / 2 if team == fav else -abs(line) / 2)
                out[team] = {"opp": opp, "home": side == "home", "kickoff": kick, "total": total,
                             "spread": (line if team == fav else (-line if line is not None else None)),
                             "implied": implied, "status": (comp.get("status") or {}).get("type", {}).get("name")}
        except Exception:
            continue
    return out


# ------------------------------------------------------------------ context
class SeasonContext:
    def __init__(self, week=None, quiet=False):
        self.api = Sleeper()
        self.now = datetime.now(timezone.utc)
        st = self.api.state() or {}
        self.season = st.get("season") or config.SEASON
        self.week = int(week or st.get("display_week") or st.get("week") or 1)
        self.league = self.api.league() or {}
        self.scoring = self.league.get("scoring_settings") or {}
        self.users = {u["user_id"]: (u.get("display_name") or u["user_id"]) for u in (self.api.users() or [])}
        self.team_names = {u["user_id"]: ((u.get("metadata") or {}).get("team_name") or u.get("display_name"))
                           for u in (self.api.users() or [])}
        self.rosters = self.api.rosters(force=True) or []
        self.by_roster = {r["roster_id"]: r for r in self.rosters}
        self.my = next((r for r in self.rosters if r.get("owner_id") == config.MY_USER_ID), None) or {}
        self.my_rid = self.my.get("roster_id", config.MY_ROSTER_ID)
        self.players, _ = self.api.players()
        self.players = self.players or {}
        self.matchups = self.api.matchups(self.week) or []
        sched, _ = self.api.schedule(self.season)
        self.byes = bye_weeks(sched)
        self.odds = fetch_espn_odds(self.week, self.season)
        # weekly projections
        proj, _ = self.api.projections_week(self.week, self.season)
        self.wproj = {}
        for p in proj or []:
            pid = str(p.get("player_id"))
            pos = ((p.get("player") or {}).get("position")) or (self.players.get(pid) or {}).get("position")
            if pos == "FB":
                pos = "RB"
            stt = p.get("stats") or {}
            self.wproj[pid] = {"pts": league_points(stt, self.scoring, pos), "opp": p.get("opponent"), "pos": pos,
                               "stats": stt}
        # FP weekly ranks
        self.fp_week = {}
        by_name_pos, by_name = _player_index(self.players)
        for slug, nm in (("half-point-ppr-flex.php", "fp_wk_flex.json"), ("qb.php", "fp_wk_qb.json"),
                         ("k.php", "fp_wk_k.json"), ("dst.php", "fp_wk_dst.json")):
            rows, meta = fetch_fp_page(slug, nm)
            if meta.get("week") and int(meta["week"]) != self.week:
                warn("FantasyPros %s is week %s (we are week %s)" % (slug, meta["week"], self.week))
            for row in rows:
                pid = _match_fp(row, self.players, by_name_pos, by_name)
                if pid:
                    self.fp_week[pid] = {"rank": row.get("rank_ecr"), "pos_rank": row.get("pos_rank"),
                                         "grade": row.get("start_sit_grade"), "opp": row.get("player_opponent"),
                                         "std": float(row.get("rank_std") or 0), "tier": row.get("tier")}
        # ROS board (same blend as the draft board, but FP rest-of-season ranks)
        ros_rows, _ = fetch_fp_page("ros-half-point-ppr-overall.php", "fp_ros.json", 6 * 3600)
        sproj, _ = self.api.projections_season(self.season)
        self.ros = {r["player_id"]: r for r in build_board(self.league, self.players, sproj, ros_rows, self.byes)}
        self.trend_add = {str(t["player_id"]): t["count"] for t in (self.api.trending("add", 48, 60) or [])}
        self.trend_drop = {str(t["player_id"]): t["count"] for t in (self.api.trending("drop", 48, 60) or [])}
        self.owned = {}
        for r in self.rosters:
            for pid in (r.get("players") or []):
                self.owned[pid] = r["roster_id"]

    # ---- helpers
    def name(self, pid):
        p = self.players.get(pid) or {}
        if p.get("position") == "DEF":
            return "%s %s" % (p.get("first_name", ""), p.get("last_name", ""))
        return p.get("full_name") or pid

    def owner_name(self, rid):
        r = self.by_roster.get(rid) or {}
        return self.users.get(r.get("owner_id"), "Team %s" % rid)

    def player_line(self, pid):
        """Everything the weekly decisions need about one player."""
        p = self.players.get(pid) or {}
        pos = p.get("position") or (self.wproj.get(pid) or {}).get("pos") or "?"
        if pos == "FB":
            pos = "RB"
        team = p.get("team")
        wp = self.wproj.get(pid) or {}
        od = self.odds.get(team) or {}
        bye = self.byes.get(team) == self.week or (wp and wp.get("opp") is None and pid in self.wproj and not od)
        inj = p.get("injury_status")
        proj = float(wp.get("pts") or 0.0)
        mult = 0.0 if bye else INJ_MULT.get(inj, 1.0)
        kick = od.get("kickoff")
        locked = bool(kick and kick <= self.now)
        fp = self.fp_week.get(pid) or {}
        ros = self.ros.get(pid) or {}
        return {"pid": pid, "name": self.name(pid), "pos": pos, "team": team, "opp": wp.get("opp") or od.get("opp"),
                "proj": proj, "eff": round(proj * mult, 2), "inj": inj, "bye": bye, "kickoff": kick, "locked": locked,
                "implied": od.get("implied"), "spread": od.get("spread"), "fp_rank": fp.get("rank"),
                "fp_pos_rank": fp.get("pos_rank"), "grade": fp.get("grade"), "fp_std": fp.get("std") or 0.0,
                "ros_value": ros.get("value"), "ros_rank": ros.get("rank"), "ros_pts": ros.get("pts"),
                "trend_add": self.trend_add.get(pid), "trend_drop": self.trend_drop.get(pid)}

    def roster_lines(self, rid):
        r = self.by_roster.get(rid) or {}
        return [self.player_line(pid) for pid in (r.get("players") or [])]


# ------------------------------------------------------------------ lineup
def optimal_lineup(lines, tilt=0.0):
    """Best legal lineup by effective projection. tilt>0 favors ceiling (underdog),
    tilt<0 favors floor. Returns dict slot_index -> line and total."""
    def score(l):
        return l["eff"] + tilt * (l["fp_std"] or 3.0)
    pool = sorted([l for l in lines if l["eff"] > 0], key=score, reverse=True)
    used = set()
    out = [None] * len(SLOT_ORDER)
    for i, slot in enumerate(SLOT_ORDER):
        if slot == "FLEX":
            continue
        for l in pool:
            if l["pid"] not in used and l["pos"] == slot:
                out[i] = l
                used.add(l["pid"])
                break
    fi = SLOT_ORDER.index("FLEX")
    for l in pool:
        if l["pid"] not in used and l["pos"] in config.FLEX_ELIGIBLE:
            out[fi] = l
            used.add(l["pid"])
            break
    total = sum(l["eff"] for l in out if l)
    return out, total


def current_lineup(ctx, rid):
    r = ctx.by_roster.get(rid) or {}
    starters = r.get("starters") or []
    out = []
    for i, slot in enumerate(SLOT_ORDER):
        pid = starters[i] if i < len(starters) else None
        out.append(ctx.player_line(pid) if pid and pid != "0" else None)
    return out


def lineup_moves(cur, opt, min_gain=1.0):
    """Diff two lineups by player -> list of (slot, out_line, in_line). Empty if the
    improvement is inside projection noise (< min_gain points)."""
    cur_total = sum(l["eff"] for l in cur if l)
    opt_total = sum(l["eff"] for l in opt if l)
    if opt_total - cur_total < min_gain:
        return []
    cur_ids = {l["pid"]: l for l in cur if l}
    opt_ids = {l["pid"]: l for l in opt if l}
    outs = [l for pid, l in cur_ids.items() if pid not in opt_ids]
    ins = [(SLOT_ORDER[i], l) for i, l in enumerate(opt) if l and l["pid"] not in cur_ids]
    moves = []
    for (slot, b), a in zip(ins, outs + [None] * len(ins)):
        moves.append((slot, a, b))
    return moves


def win_probability(my_total, opp_total):
    return 0.5 * (1 + math.erf((my_total - opp_total) / (TEAM_SD * math.sqrt(2)) / math.sqrt(1)))


def matchup_for(ctx, rid):
    mine = next((m for m in ctx.matchups if m.get("roster_id") == rid), None)
    if not mine or mine.get("matchup_id") is None:
        return None, None
    opp = next((m for m in ctx.matchups if m.get("matchup_id") == mine["matchup_id"] and m.get("roster_id") != rid), None)
    return mine, opp


def team_projection(ctx, rid):
    """Projected total for an opponent: their set lineup, assuming they will do the
    obvious housekeeping — any empty slot or starter who is Out/IR/suspended/on bye
    gets replaced by their best eligible bench player. Their healthy choices are kept
    as-is (we do not assume they optimize)."""
    cur = current_lineup(ctx, rid)
    lines = {l["pid"]: l for l in ctx.roster_lines(rid)}
    used = {l["pid"] for l in cur if l}
    bench = sorted([l for pid, l in lines.items() if pid not in used and l["eff"] > 0], key=lambda l: -l["eff"])
    out = []
    for i, slot in enumerate(SLOT_ORDER):
        l = cur[i]
        if l and l["eff"] > 0:
            out.append(l)
            continue
        elig = (config.FLEX_ELIGIBLE if slot == "FLEX" else (slot,))
        rep = next((b for b in bench if b["pos"] in elig), None)
        if rep:
            bench.remove(rep)
            rep = dict(rep)
            rep["filled_in"] = True
            out.append(rep)
        else:
            out.append(l)
    return out, sum(l["eff"] for l in out if l)


# ------------------------------------------------------------------ waivers
def free_agents(ctx, limit_per_pos=12):
    cands = {}
    for pid, e in ctx.ros.items():
        if pid in ctx.owned or e["value"] is None:
            continue
        p = ctx.players.get(pid) or {}
        if p.get("status") in ("Inactive", "Retired") and p.get("position") != "DEF":
            continue
        if not p.get("team") and p.get("position") != "DEF":
            continue
        cands[pid] = e
    for pid, n in ctx.trend_add.items():
        if pid not in ctx.owned and pid in ctx.players and n >= 5000:
            e = cands.get(pid) or {"value": 0.0, "pos": ctx.players[pid].get("position"), "rank": None}
            # breaking-news signal: the crowd knows something the stale projection doesn't
            e = dict(e)
            e["value"] = max(e.get("value") or 0.0, 0.0) + min(8.0, 2.0 * math.log10(n / 1000.0 + 1))
            e["trend_boost"] = True
            cands[pid] = e
    out = defaultdict(list)
    for pid, e in cands.items():
        l = ctx.player_line(pid)
        l["ros_value"] = e.get("value") or 0.0
        l["ros_rank"] = e.get("rank")
        l["trend_boost"] = e.get("trend_boost", False)
        out[l["pos"]].append(l)
    for pos in out:
        out[pos].sort(key=lambda l: (-(l["ros_value"] or 0), -(l["trend_add"] or 0)))
        out[pos] = out[pos][:limit_per_pos]
    return out


def faab_bid(gain, remaining, weeks_left):
    """Suggested bid from ROS value gain over the player being dropped."""
    if gain >= 40:
        frac = 0.35
    elif gain >= 25:
        frac = 0.20
    elif gain >= 12:
        frac = 0.10
    elif gain >= 5:
        frac = 0.04
    else:
        frac = 0.01
    bid = max(1, int(round(remaining * frac)))
    return min(bid, remaining)


def upcoming_byes(ctx, lines, horizon=4):
    """{week: [lines on bye]} for my roster over the next `horizon` weeks."""
    out = defaultdict(list)
    for l in lines:
        b = ctx.byes.get(l["team"])
        if b and ctx.week <= b < ctx.week + horizon:
            out[b].append(l)
    return out


WAIVER_TYPES = {0: "rolling priority", 1: "reverse-standings priority", 2: "FAAB bidding"}


def waiver_mode(ctx):
    wt = int((ctx.league.get("settings") or {}).get("waiver_type") or 0)
    return wt, WAIVER_TYPES.get(wt, "unknown")


def season_started(ctx):
    """True once the first game of the current week has kicked off (players then go through waivers)."""
    kicks = [o["kickoff"] for o in ctx.odds.values() if o.get("kickoff")]
    return bool(kicks) and min(kicks) <= ctx.now


def recently_dropped(ctx, days=2):
    """player_id -> drop timestamp for players dropped in the last `days` (they sit on waivers)."""
    out = {}
    cutoff = time.time() - days * 86400
    for w in (ctx.week, max(1, ctx.week - 1)):
        for t in ctx.api.transactions(w) or []:
            ts = (t.get("created") or 0) / 1000
            if ts >= cutoff:
                for pid in (t.get("drops") or {}):
                    out[pid] = ts
    return out


def waiver_plan(ctx, max_claims=4):
    my_lines = ctx.roster_lines(ctx.my_rid)
    wt, wt_name = waiver_mode(ctx)
    started = season_started(ctx)
    dropped = recently_dropped(ctx) if started else {}
    counts = Counter(l["pos"] for l in my_lines)
    my_ros = [ctx.ros.get(l["pid"], {"value": 0.0, "pos": l["pos"], "player_id": l["pid"], "name": l["name"]}) for l in my_lines]
    base = lineup_value([r for r in my_ros if "value" in r])
    remaining = config.FAAB_BUDGET - int((ctx.my.get("settings") or {}).get("waiver_budget_used") or 0)
    weeks_left = max(1, 17 - ctx.week + 1)
    fa = free_agents(ctx)
    # droppable = lowest ROS value bench players (never K/DEF unless replacing them, never top-9)
    ranked_mine = sorted(my_lines, key=lambda l: -(l["ros_value"] or 0))
    core = {l["pid"] for l in ranked_mine[:9]}
    claims = []
    for pos, cands in fa.items():
        for c in cands[:6]:
            cand_entry = dict(ctx.ros.get(c["pid"]) or {"pos": pos, "player_id": c["pid"], "name": c["name"], "repl": 0})
            cand_entry["value"] = c["ros_value"]
            best = None
            for d in my_lines:
                if d["pid"] in core and d["pos"] != pos:
                    continue
                if d["pos"] in ("K", "DEF") and pos != d["pos"]:
                    continue
                # never drop the only backup QB/TE unless the add plays that position
                if d["pos"] in ("QB", "TE") and pos != d["pos"] and counts.get(d["pos"], 0) <= 2:
                    continue
                if d["pid"] in core and d["pos"] == pos and (d["ros_value"] or 0) >= (c["ros_value"] or 0):
                    continue
                roster_after = [r for r in my_ros if r.get("player_id") != d["pid"]] + [cand_entry]
                gain = lineup_value([r for r in roster_after if "value" in r]) - base
                raw_gain = (c["ros_value"] or 0) - (d["ros_value"] or 0)
                score = gain + 0.15 * max(raw_gain, 0)
                if best is None or score > best[0]:
                    best = (score, gain, raw_gain, d)
            if best and (best[1] > 0.5 or (pos in ("K", "DEF") and best[2] > 1)):
                claims.append({"add": c, "drop": best[3], "gain": round(best[1], 1), "raw_gain": round(best[2], 1),
                               "bid": faab_bid(max(best[1], best[2] * 0.5), remaining, weeks_left) if wt == 2 else None,
                               "on_waivers": c["pid"] in dropped or (started and False)})
    claims.sort(key=lambda x: (-x["gain"], -x["raw_gain"]))
    # de-dup drops: one drop per claim in priority order
    used_drops, final = set(), []
    for cl in claims:
        if cl["drop"]["pid"] in used_drops:
            continue
        used_drops.add(cl["drop"]["pid"])
        final.append(cl)
        if len(final) >= max_claims:
            break
    # DEF: only suggest a stream if a free agent out-ranks mine this week (FantasyPros weekly DST rank)
    my_def = next((l for l in my_lines if l["pos"] == "DEF"), None)
    my_def_rank = _rank_num(my_def["fp_pos_rank"]) if my_def else 99
    def_streams = []
    for l in fa.get("DEF", [])[:8]:
        r = _rank_num(l["fp_pos_rank"])
        opp = l.get("opp")
        od = ctx.odds.get(opp) if opp else None
        l["opp_implied"] = od.get("implied") if od else None
        if r < my_def_rank:
            def_streams.append(l)
    def_streams.sort(key=lambda l: _rank_num(l["fp_pos_rank"]))
    bench_pool = [l for l in my_lines if l["pid"] not in core and l["pos"] not in ("K", "DEF")
                  and not (l["pos"] in ("QB", "TE") and counts.get(l["pos"], 0) <= 2)]
    cheapest = min(bench_pool, key=lambda l: (l["ros_value"] or 0)) if bench_pool else None
    return {"claims": final, "remaining": remaining, "byes": upcoming_byes(ctx, my_lines), "cheapest_drop": cheapest,
            "mode": wt, "mode_name": wt_name, "started": started,
            "priority": (ctx.my.get("settings") or {}).get("waiver_position"), "teams": len(ctx.rosters),
            "my_def": my_def, "def_streams": def_streams[:3],
            "trending": sorted([(ctx.player_line(pid), n) for pid, n in ctx.trend_add.items() if pid not in ctx.owned],
                               key=lambda t: -t[1])[:8],
            "ir": [l for l in my_lines if l["inj"] in ("Out", "IR", "PUP") and not (ctx.my.get("reserve") or [])],
            "streams": {pos: fa.get(pos, [])[:3] for pos in ("DEF", "K", "TE")}}


def _rank_num(pos_rank):
    try:
        return int(re.sub(r"[^0-9]", "", str(pos_rank or "")) or 99)
    except ValueError:
        return 99


# ------------------------------------------------------------------ trades
def trade_ideas(ctx, top_n=5):
    my_lines = ctx.roster_lines(ctx.my_rid)

    def ros_entries(lines):
        return [ctx.ros[l["pid"]] for l in lines if l["pid"] in ctx.ros]

    mine = ros_entries(my_lines)
    my_base = lineup_value(mine)
    my_sorted = sorted(mine, key=lambda e: -e["value"])
    give_pool = [e for e in my_sorted[2:] if e["pos"] not in ("K", "DEF")][:8]   # keep my two best untouchable
    ideas = []
    for r in ctx.rosters:
        rid = r["roster_id"]
        if rid == ctx.my_rid:
            continue
        theirs = ros_entries(ctx.roster_lines(rid))
        their_base = lineup_value(theirs)
        get_pool = sorted([e for e in theirs if e["pos"] not in ("K", "DEF")], key=lambda e: -e["value"])[:9]
        combos = [([g], [t]) for g in give_pool for t in get_pool]
        combos += [([g1, g2], [t]) for i, g1 in enumerate(give_pool) for g2 in give_pool[i + 1:] for t in get_pool]
        for give, get in combos:
            gid = {e["player_id"] for e in give}
            tid = {e["player_id"] for e in get}
            my_after = [e for e in mine if e["player_id"] not in gid] + get
            their_after = [e for e in theirs if e["player_id"] not in tid] + give
            if len(my_after) > 15 or len(their_after) > 15:
                continue
            my_gain = lineup_value(my_after) - my_base
            their_gain = lineup_value(their_after) - their_base
            v_give = sum(max(e["value"], 0) for e in give)
            v_get = sum(max(e["value"], 0) for e in get)
            if v_get > 1.3 * v_give + 5:          # nobody accepts a lopsided raw-value swap
                continue
            if my_gain >= 4 and their_gain >= -2:
                ideas.append({"rid": rid, "owner": ctx.owner_name(rid), "give": give, "get": get,
                              "my_gain": round(my_gain, 1), "their_gain": round(their_gain, 1),
                              "fairness": round(their_gain + my_gain, 1)})
    ideas.sort(key=lambda x: (-(x["my_gain"] + 0.5 * min(x["their_gain"], 10)), -x["their_gain"]))
    # one idea per partner
    seen, out = set(), []
    for i in ideas:
        if i["rid"] in seen:
            continue
        seen.add(i["rid"])
        out.append(i)
        if len(out) >= top_n:
            break
    return out, my_base


def evaluate_trade(ctx, give_names, get_names, partner_rid=None):
    """Evaluate an explicit trade: names I give, names I get."""
    from .util import norm_name
    idx = {}
    for pid in ctx.owned:
        idx[norm_name(ctx.name(pid))] = pid
        p = ctx.players.get(pid) or {}
        if p.get("last_name"):
            idx.setdefault(norm_name(p["last_name"]), pid)

    def find(n):
        k = norm_name(n)
        if k in idx:
            return idx[k]
        for key, pid in idx.items():
            if k and k in key:
                return pid
        return None
    give = [find(n) for n in give_names]
    get = [find(n) for n in get_names]
    missing = [n for n, p in zip(give_names + get_names, give + get) if not p]
    if missing:
        return {"error": "could not match: %s" % ", ".join(missing)}
    partner = partner_rid or ctx.owned.get(get[0])
    mine = [ctx.ros[l["pid"]] for l in ctx.roster_lines(ctx.my_rid) if l["pid"] in ctx.ros]
    theirs = [ctx.ros[l["pid"]] for l in ctx.roster_lines(partner) if l["pid"] in ctx.ros]
    ge = [ctx.ros[p] for p in give if p in ctx.ros]
    te = [ctx.ros[p] for p in get if p in ctx.ros]
    my_after = [e for e in mine if e["player_id"] not in set(give)] + te
    their_after = [e for e in theirs if e["player_id"] not in set(get)] + ge
    return {"partner": ctx.owner_name(partner), "give": ge, "get": te,
            "my_gain": round(lineup_value(my_after) - lineup_value(mine), 1),
            "their_gain": round(lineup_value(their_after) - lineup_value(theirs), 1),
            "value_give": round(sum(e["value"] for e in ge), 1), "value_get": round(sum(e["value"] for e in te), 1),
            "roster_size_after": len(my_after)}


def pending_trades(ctx):
    out = []
    for w in (ctx.week, max(1, ctx.week - 1)):
        for t in ctx.api.transactions(w) or []:
            if t.get("type") == "trade" and t.get("status") not in ("complete", "failed") and ctx.my_rid in (t.get("roster_ids") or []):
                out.append(t)
    return out


# ------------------------------------------------------------------ recap
def recap(ctx, week=None):
    wk = week or (ctx.week - 1)
    if wk < 1:
        return None
    ms = ctx.api.matchups(wk) or []
    if not ms or all((m.get("points") or 0) == 0 for m in ms):
        return {"week": wk, "empty": True}
    by_mid = defaultdict(list)
    for m in ms:
        by_mid[m.get("matchup_id")].append(m)
    games = []
    for mid, pair in by_mid.items():
        if len(pair) != 2:
            continue
        a, b = sorted(pair, key=lambda m: -(m.get("points") or 0))
        games.append({"winner": a["roster_id"], "loser": b["roster_id"], "w_pts": a.get("points") or 0, "l_pts": b.get("points") or 0,
                      "mine": ctx.my_rid in (a["roster_id"], b["roster_id"])})
    scores = {m["roster_id"]: m.get("points") or 0 for m in ms}
    perfs = []
    for m in ms:
        for pid, pts in (m.get("players_points") or {}).items():
            if pid in (m.get("starters") or []):
                perfs.append((pts, pid, m["roster_id"]))
    perfs.sort(reverse=True)
    my_m = next((m for m in ms if m["roster_id"] == ctx.my_rid), None)
    bench_regret = []
    if my_m:
        pp = my_m.get("players_points") or {}
        starters = my_m.get("starters") or []
        for pid, pts in pp.items():
            if pid not in starters:
                pos = (ctx.players.get(pid) or {}).get("position")
                worst = min(((pp.get(s, 0), s) for s in starters if (ctx.players.get(s) or {}).get("position") == pos), default=None)
                if worst and pts > worst[0] + 3:
                    bench_regret.append((pid, pts, worst[1], worst[0]))
    standings = sorted(ctx.rosters, key=lambda r: (-(r["settings"].get("wins", 0)), -(r["settings"].get("fpts", 0) + r["settings"].get("fpts_decimal", 0) / 100.0)))
    return {"week": wk, "games": games, "scores": scores, "top": perfs[:5], "bottom": [p for p in perfs if p[0] < 5][-5:],
            "standings": standings, "bench_regret": bench_regret, "my": my_m}
