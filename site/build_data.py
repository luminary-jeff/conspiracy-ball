#!/usr/bin/env python3
"""Build site/data.json (and site/index.html from template.html) for the Conspiracy Ball page.
Run daily. Reads Sleeper live; keeps the previous odds to report line movement."""
import json, os, sys, time, warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ff import config
from ff.season import SeasonContext, recap
from ff.sim import run_sim, american
from ff.draft import lineup_value
from ff.sleeper import Sleeper

SITE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SITE, "data.json")
prev = {}
if os.path.exists(OUT):
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = {}

ctx = SeasonContext()
api = ctx.api
trades = [t for t in (prev.get("assumed_trades") or []) if t]
rows = run_sim(ctx, sims=int(os.environ.get("SIMS", "20000")), trades=trades)
now = datetime.now(timezone.utc)


def team_label(rid, users, rosters_by_id):
    r = rosters_by_id.get(rid) or {}
    u = users.get(r.get("owner_id")) or {}
    md = u.get("metadata") or {}
    return {"owner": u.get("display_name") or "Team %s" % rid, "team": md.get("team_name") or u.get("display_name") or "Team %s" % rid}


users_full = {u["user_id"]: u for u in (api.users() or [])}
labels = {r["roster_id"]: team_label(r["roster_id"], users_full, ctx.by_roster) for r in ctx.rosters}
prev_book = {b["owner"]: b for b in ((prev.get("seasons") or {}).get(ctx.season) or {}).get("book", [])}

book = []
for r in rows:
    lab = labels[r["roster_id"]]
    st = ctx.by_roster[r["roster_id"]]["settings"]
    pb = prev_book.get(lab["owner"])
    move = round((r["title"] - pb["title_p"]) * 100, 1) if pb else 0.0
    book.append({"owner": lab["owner"], "team": lab["team"], "title_p": r["title"], "title_odds": american(r["title"], vig=0.06),
                 "playoff_p": r["playoff"], "playoff_odds": american(r["playoff"], vig=0.04), "bye_p": r["bye"],
                 "exp_wins": r["exp_wins"], "pts_wk": r["pts_wk"], "wk_proj": r["wk_proj"], "move": move,
                 "wins": st.get("wins", 0), "losses": st.get("losses", 0), "pf": round(st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100, 1),
                 "me": r["roster_id"] == ctx.my_rid})

# ---------------- wire (newest first)
wire = []


def add(ts, kind, tag, headline, body="", who=None):
    wire.append({"ts": int(ts), "date": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "kind": kind, "tag": tag,
                 "headline": headline, "body": body, "who": who})


draft = api.draft()
if draft.get("start_time"):
    ds = draft["start_time"] / 1000
    picks = api.draft_picks() or []
    if picks:
        p1 = picks[0]
        add(ds, "draft", "CONFIRMED", "Draft complete. The board is open.",
            "%s went 1.01 to %s. %d picks, 15 rounds, one keeper allowed ==and nobody used it.==" % (
                "%s %s" % (p1["metadata"].get("first_name", ""), p1["metadata"].get("last_name", "")), labels.get(p1["draft_slot"], {}).get("owner", "?"), len(picks)))
        # draft grades by ROS lineup value
        grades = []
        for r in ctx.rosters:
            ents = [ctx.ros[p] for p in (r.get("players") or []) if p in ctx.ros]
            grades.append((lineup_value(ents), labels[r["roster_id"]]["owner"]))
        grades.sort(reverse=True)
        add(ds + 3600 * 3, "odds", "SOURCES SAY", "Opening line: %s installed as favorite at %s." % (book[0]["owner"], book[0]["title_odds"]),
            "Draft-room power ranking: " + ", ".join("%d. %s" % (i, o) for i, (v, o) in enumerate(grades, 1)) + ". Values are projected starting-lineup points over replacement, ==so take it up with RotoWire.==")

# transactions
for w in range(1, ctx.week + 1):
    for t in api.transactions(w) or []:
        if t.get("status") != "complete":
            continue
        ts = (t.get("created") or 0) / 1000
        adds = {ctx.name(p): rid for p, rid in (t.get("adds") or {}).items()}
        drops = {ctx.name(p): rid for p, rid in (t.get("drops") or {}).items()}
        rids = t.get("roster_ids") or []
        if t["type"] == "trade" and len(rids) == 2:
            a, b = rids
            to_a = [n for n, r in adds.items() if r == a]
            to_b = [n for n, r in adds.items() if r == b]
            add(ts, "trade", "CONFIRMED", "TRADE: %s sends %s to %s for %s." % (labels[a]["owner"], ", ".join(to_b) or "nothing", labels[b]["owner"], ", ".join(to_a) or "nothing"),
                "League review window: two days, six vetoes to block. ==Group chat opinions do not count as vetoes.==")
        elif t["type"] in ("free_agent", "waiver") and rids:
            rid = rids[0]
            o = labels[rid]["owner"]
            bid = (t.get("settings") or {}).get("waiver_bid")
            if adds and t["type"] == "waiver":
                head = "%s wins %s on waivers for $%s." % (o, ", ".join(adds), bid if bid is not None else "?")
            elif adds:
                head = "%s signs %s off the street." % (o, ", ".join(adds))
            else:
                head = "%s makes a roster move." % o
            body = ("Cut: %s." % ", ".join(drops)) if drops else "No corresponding cut. ==Roster spot was open, which is its own kind of confession.=="
            add(ts, t["type"], "CONFIRMED", head, body, who=o)

# completed weeks: results + awards
for w in range(1, ctx.week):
    rc = recap(ctx, w)
    if not rc or rc.get("empty"):
        continue
    ms = api.matchups(w) or []
    ts = time.time() - (ctx.week - w) * 7 * 86400
    res = "; ".join("%s %.0f def. %s %.0f" % (labels[g["winner"]]["owner"], g["w_pts"], labels[g["loser"]]["owner"], g["l_pts"]) for g in rc["games"])
    hi = max(rc["scores"], key=rc["scores"].get)
    lo = min(rc["scores"], key=rc["scores"].get)
    add(ts, "result", "CONFIRMED", "Week %d final: %s high with %.0f, %s low with %.0f." % (w, labels[hi]["owner"], rc["scores"][hi], labels[lo]["owner"], rc["scores"][lo]), res)
    # grassy knoll: worst bench-over-starter gap league-wide
    worst = None
    for m in ms:
        pp = m.get("players_points") or {}
        starters = m.get("starters") or []
        for pid, pts in pp.items():
            if pid in starters:
                continue
            pos = (ctx.players.get(pid) or {}).get("position")
            for s in starters:
                if (ctx.players.get(s) or {}).get("position") == pos and pts - pp.get(s, 0) > (worst[0] if worst else 6):
                    worst = (pts - pp.get(s, 0), m["roster_id"], pid, s, pts, pp.get(s, 0))
    if worst:
        gap, rid, pid, s, pts, spts = worst
        add(ts + 60, "award", "GRASSY KNOLL", "Grassy Knoll Award, week %d: %s" % (w, labels[rid]["owner"]),
            "Benched %s (%.1f) and started %s (%.1f). %.1f points left on the sofa. ==Investigators are calling it a lone-gunman decision.==" % (ctx.name(pid), pts, ctx.name(s), spts, gap))

# line movement vs previous build
movers = sorted([b for b in book if abs(b["move"]) >= 0.8], key=lambda b: -abs(b["move"]))
if movers and prev.get("generated"):
    add(time.time(), "odds", "LINE MOVE", "Line movement: " + ", ".join("%s %+.1f pts (now %s)" % (b["owner"], b["move"], b["title_odds"]) for b in movers[:4]),
        "Title probability change since the last update, from 20,000 simulated seasons with fresh projections and results.")

# manual extras (site/extra_wire.json: [{"ts": epoch, "tag": "...", "headline": "...", "body": "..."}])
extra = os.path.join(SITE, "extra_wire.json")
if os.path.exists(extra):
    for e in json.load(open(extra)):
        add(e.get("ts", time.time()), e.get("kind", "note"), e.get("tag", "UNVERIFIED"), e["headline"], e.get("body", ""))

wire.sort(key=lambda x: -x["ts"])

# ---------------- 2025 archive
archive = {}
prev_id = ctx.league.get("previous_league_id")
if prev_id:
    base = "%s/league/%s" % (config.API_BASE, prev_id)
    pl = api.get_live(base) or {}
    pr = api.get_live(base + "/rosters") or []
    pu = {u["user_id"]: u for u in (api.get_live(base + "/users") or [])}
    br = api.get_live(base + "/winners_bracket") or []
    lab2 = {}
    for r in pr:
        u = pu.get(r.get("owner_id")) or {}
        md = u.get("metadata") or {}
        lab2[r["roster_id"]] = {"owner": u.get("display_name") or "Team %s" % r["roster_id"], "team": md.get("team_name") or u.get("display_name") or "Team %s" % r["roster_id"]}
    st = []
    for r in sorted(pr, key=lambda r: (-(r["settings"].get("wins", 0)), -(r["settings"].get("fpts", 0)))):
        s = r["settings"]
        st.append({"owner": lab2[r["roster_id"]]["owner"], "team": lab2[r["roster_id"]]["team"], "wins": s.get("wins", 0), "losses": s.get("losses", 0),
                   "pf": round(s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100, 1), "record": (r.get("metadata") or {}).get("record", ""),
                   "me": r.get("owner_id") == config.MY_USER_ID})
    champ_rid = int((pl.get("metadata") or {}).get("latest_league_winner_roster_id") or 0)
    final = next((m for m in br if m.get("p") == 1), None)
    third = next((m for m in br if m.get("p") == 3), None)
    archive = {"season": pl.get("season"), "status": "complete", "standings": st,
               "champion": lab2.get(champ_rid), "runner_up": lab2.get(final["l"]) if final else None,
               "third": lab2.get(third["w"]) if third else None,
               "best_record": st[0] if st else None,
               "bracket": [{"round": m.get("r"), "winner": lab2.get(m.get("w"), {}).get("owner"), "loser": lab2.get(m.get("l"), {}).get("owner"), "place": m.get("p")} for m in br]}

data = {"generated": now.isoformat(), "league": config.LEAGUE_NAME, "season": ctx.season, "week": ctx.week,
        "assumed_trades": trades,
        "seasons": {ctx.season: {"status": "in_season", "week": ctx.week, "book": book, "wire": wire[:60],
                                 "standings": sorted(book, key=lambda b: (-b["wins"], -b["pf"]))},
                    **({archive["season"]: archive} if archive else {})}}
json.dump(data, open(OUT, "w"), indent=1)
tpl = open(os.path.join(SITE, "template.html")).read()
open(os.path.join(SITE, "index.html"), "w").write(tpl.replace("__DATA__", json.dumps(data)))
print("wrote %s (%d wire items, %d book rows, archive=%s)" % (OUT, len(wire), len(book), bool(archive)))
