"""Decision-ready in-season briefs: lineup, preview, waivers, trades, recap, and the
day-aware `brief` that picks the right sections."""
import time
from datetime import datetime, timezone

from . import config
from .season import (SeasonContext, SLOT_ORDER, optimal_lineup, current_lineup, lineup_moves, win_probability,
                     matchup_for, team_projection, waiver_plan, trade_ideas, evaluate_trade, pending_trades, recap)
from .ui import B, DIM, RED, GRN, YEL, CYN, RST, c, bold, hr, pos_c, short
from .util import warn


def _kick(l):
    if not l or not l.get("kickoff"):
        return "   "
    k = l["kickoff"].astimezone()
    return k.strftime("%a %-I%p").replace("PM", "p").replace("AM", "a")


def _pl(l, w=20):
    """one-line player descriptor"""
    if not l:
        return c(RED, "-- empty --")
    flags = []
    if l["bye"]:
        flags.append(c(RED, "BYE"))
    elif l["inj"]:
        col = RED if l["inj"] in ("Out", "IR", "Doubtful") else YEL
        flags.append(c(col, l["inj"][:4].upper()))
    if l["locked"]:
        flags.append(c(DIM, "locked"))
    opp = ("%s%s" % ("" if l.get("home", True) else "@", l["opp"] or "?")) if l.get("opp") else "bye"
    grade = (" %s" % l["grade"]) if l.get("grade") else ""
    rk = (" %s" % l["fp_pos_rank"]) if l.get("fp_pos_rank") else ""
    return "%s %-*s %-4s %-5s %5.1f%s%s %s %s" % (
        pos_c(l["pos"]), w, short(l["name"], w), l["team"] or "FA", opp, l["proj"],
        c(DIM, rk), c(DIM, grade), _kick(l), " ".join(flags))


# ------------------------------------------------------------------ lineup
def render_lineup(ctx):
    L = []
    my_lines = ctx.roster_lines(ctx.my_rid)
    cur = current_lineup(ctx, ctx.my_rid)
    mine_m, opp_m = matchup_for(ctx, ctx.my_rid)
    tilt = 0.0
    wp = None
    if opp_m:
        _, opp_total = team_projection(ctx, opp_m["roster_id"])
        _, my_opt_total = optimal_lineup(my_lines)
        wp = win_probability(my_opt_total, opp_total)
        tilt = 0.25 if wp < 0.40 else (-0.15 if wp > 0.65 else 0.0)
    opt, total = optimal_lineup(my_lines, tilt=tilt)
    moves = lineup_moves(cur, opt)
    L.append(bold("LINEUP — week %d" % ctx.week) + c(DIM, "  projected %.1f" % total) +
             (c(DIM, "  | win prob %d%% -> %s" % (round(wp * 100), "ceiling tilt" if tilt > 0 else ("floor tilt" if tilt < 0 else "neutral"))) if wp is not None else ""))
    if not moves:
        L.append(c(B + GRN, "✔ Your lineup is already optimal (any change is inside projection noise). No moves."))
        opt = [l if l else None for l in cur] if sum(1 for l in cur if l) >= 9 else opt
    else:
        L.append(c(B + YEL, "★ MAKE %d MOVE%s in Sleeper:" % (len(moves), "" if len(moves) == 1 else "S")))
        for slot, a, b in moves:
            if b and b["locked"] and (not a or a["pid"] != b["pid"]):
                L.append("   %s %-4s %s" % (c(RED, "!"), slot, "%s is locked (already kicked off) — cannot move in" % b["name"]))
                continue
            L.append("   %s %s  %s" % (c(RED, "OUT " + (short(a["name"], 18) if a else "(empty slot)")),
                                       c(GRN, "IN  " + (short(b["name"], 18) if b else "(empty)")),
                                       c(DIM, "-> %s slot (%.1f -> %.1f)" % (slot, a["eff"] if a else 0, b["eff"] if b else 0))))
    L.append(hr())
    for i, slot in enumerate(SLOT_ORDER):
        L.append(" %-4s %s" % (slot, _pl(opt[i])))
    bench = [l for l in my_lines if l["pid"] not in {x["pid"] for x in opt if x}]
    bench.sort(key=lambda l: -l["proj"])
    L.append(c(DIM, " bench: ") + ", ".join("%s %s %.1f%s" % (l["pos"], short(l["name"].split()[-1] if l["pos"] != "DEF" else l["team"], 12), l["proj"],
                                                          c(RED, " " + l["inj"][:3].upper()) if l["inj"] else "") for l in bench))
    # re-check list
    checks = [l for l in opt if l and (l["inj"] in ("Questionable", "Doubtful") or l["bye"])]
    thu = [l for l in opt if l and l["kickoff"] and l["kickoff"].astimezone().weekday() in (3, 4)]
    if checks:
        L.append(c(YEL, " ⚠ re-check before kickoff: ") + ", ".join("%s (%s)" % (l["name"], l["inj"] or "bye") for l in checks))
        for l in checks:
            alt = next((x for x in bench if x["pos"] == l["pos"] and x["eff"] > 0 and not x["locked"]), None) or \
                  next((x for x in bench if x["pos"] in config.FLEX_ELIGIBLE and l["pos"] in config.FLEX_ELIGIBLE and x["eff"] > 0 and not x["locked"]), None)
            if alt:
                L.append(c(DIM, "    if %s is out -> start %s (%.1f)" % (l["name"].split()[-1], alt["name"], alt["proj"])))
    if thu:
        L.append(c(DIM, " early lock (Thu/Fri game): ") + ", ".join(l["name"] for l in thu))
    return "\n".join(L)


# ------------------------------------------------------------------ preview
def render_preview(ctx):
    L = []
    mine_m, opp_m = matchup_for(ctx, ctx.my_rid)
    if not opp_m:
        return bold("PREVIEW — week %d" % ctx.week) + "\n  no opponent found (bye week or matchups not published yet)"
    orid = opp_m["roster_id"]
    my_lu, my_total = optimal_lineup(ctx.roster_lines(ctx.my_rid))
    op_lu, op_total = team_projection(ctx, orid)
    wp = win_probability(my_total, op_total)
    r = ctx.by_roster.get(orid, {}).get("settings", {})
    L.append(bold("PREVIEW — week %d vs %s" % (ctx.week, ctx.owner_name(orid))) +
             c(DIM, " (%s-%s)" % (r.get("wins", 0), r.get("losses", 0))))
    col = GRN if wp >= 0.55 else (RED if wp <= 0.45 else YEL)
    L.append("  projected  YOU %s  vs  THEM %s   win probability %s" % (
        bold("%.1f" % my_total), bold("%.1f" % op_total), c(B + col, "%d%%" % round(wp * 100))))
    fills = [l for l in op_lu if l and l.get("filled_in")]
    if fills:
        L.append(c(DIM, "  assumes they fill holes with: " + ", ".join("%s %s" % (l["pos"], l["name"]) for l in fills)))
    _, op_best = optimal_lineup(ctx.roster_lines(orid))
    if op_best - op_total > 3:
        L.append(c(DIM, "  (if they fully optimize: %.1f -> your win prob %d%%)" % (
            op_best, round(win_probability(my_total, op_best) * 100))))
    L.append(hr())
    L.append("  %-4s %-38s | %s" % ("slot", "you", "them"))
    for i, slot in enumerate(SLOT_ORDER):
        a, b = my_lu[i], op_lu[i]
        L.append("  %-4s %-38s | %s" % (slot, _short_pl(a), _short_pl(b)))
    # what has to go right: biggest slot deficits
    gaps = sorted([(((b["eff"] if b else 0) - (a["eff"] if a else 0)), SLOT_ORDER[i], a, b) for i, (a, b) in enumerate(zip(my_lu, op_lu))], reverse=True)
    L.append(hr())
    edge = [g for g in gaps if g[0] > 2][:3]
    if edge:
        L.append(c(YEL, "  their edges: ") + "; ".join("%s %s +%.1f over %s" % (g[1], short(g[3]["name"], 16), g[0], short(g[2]["name"], 14) if g[2] else "empty") for g in edge))
    mine_edge = [g for g in reversed(gaps) if g[0] < -2][:3]
    if mine_edge:
        L.append(c(GRN, "  your edges:  ") + "; ".join("%s %s +%.1f over %s" % (g[1], short(g[2]["name"], 16), -g[0], short(g[3]["name"], 14) if g[3] else "empty") for g in mine_edge))
    risk = [l for l in op_lu if l and (l["inj"] or l["bye"])]
    if risk:
        L.append(c(DIM, "  their injury/bye exposure: ") + ", ".join("%s (%s)" % (l["name"], l["inj"] or "bye") for l in risk))
    return "\n".join(L)


def _short_pl(l):
    if not l:
        return c(RED, "-- empty --")
    inj = (" " + c(RED if l["inj"] in ("Out", "Doubtful") else YEL, l["inj"][:1])) if l["inj"] else ""
    if l.get("filled_in"):
        inj += c(DIM, " *")
    return "%s %-18s %5.1f%s" % (pos_c(l["pos"]), short(l["name"], 18), l["eff"], inj)


# ------------------------------------------------------------------ waivers
def render_waivers(ctx):
    plan = waiver_plan(ctx)
    hdr = bold("ROSTER MOVES — week %d" % ctx.week)
    if plan["mode"] == 2:
        hdr += c(DIM, "  FAAB left $%d of $%d" % (plan["remaining"], config.FAAB_BUDGET))
    else:
        hdr += c(DIM, "  waiver priority: you are %s of %d (%s)" % (plan["priority"] or "?", plan["teams"], plan["mode_name"]))
    L = [hdr]
    if not plan["started"]:
        L.append(c(DIM, "  Every unowned player is a free agent until Thursday kickoff: adds are instant, no claim needed."))
    else:
        L.append(c(DIM, "  Players dropped in the last 2 days sit on waivers (claims process Wed morning, submit by Tue night); everyone else is an instant add."))
    if plan["ir"]:
        L.append(c(B + RED, "★ IR first: ") + ", ".join("%s (%s)" % (l["name"], l["inj"]) for l in plan["ir"]) +
                 c(DIM, "  -> Sleeper shows an IR tag next to eligible players; moving one frees a bench slot"))
    if not plan["claims"]:
        L.append(c(GRN, "✔ No pickup improves your roster enough to justify a drop. Stand pat."))
    else:
        L.append(c(B + YEL, "★ RECOMMENDED, in priority order:"))
        for i, cl in enumerate(plan["claims"], 1):
            a, d = cl["add"], cl["drop"]
            how = "CLAIM" if cl.get("on_waivers") else "ADD NOW"
            bid = (" bid " + c(B, "$%d" % cl["bid"])) if cl.get("bid") is not None else ""
            L.append("  %d. %s %s %-20s %-4s%s  %s %s %-20s" % (
                i, c(B, how), pos_c(a["pos"]), short(a["name"], 20), a["team"] or "FA", bid,
                c(RED, "DROP"), pos_c(d["pos"]), short(d["name"], 20)))
            why = []
            if a.get("trend_add"):
                why.append("%s Sleeper managers added him in 48h%s" % (
                    ("%dk" % (a["trend_add"] // 1000)) if a["trend_add"] >= 1000 else str(a["trend_add"]),
                    " — breaking news; check why before you act" if a.get("trend_boost") else ""))
            if cl["gain"] > 0:
                why.append("projected lineup +%.1f pts/wk" % cl["gain"])
            if a.get("ros_rank"):
                why.append("rest-of-season #%d overall" % a["ros_rank"])
            L.append(c(DIM, "     why: " + "; ".join(why)))
            L.append(c(DIM, "     rivals: " + _rivals_line(cl["block"], cl["leak"], d)))
    L.append(hr())
    md = plan.get("my_def")
    if md:
        if plan["def_streams"]:
            L.append(c(YEL, " DEF: ") + "%s is %s this week; better free-agent defenses: " % (md["team"], md.get("fp_pos_rank") or "unranked") +
                     ", ".join("%s (%s, vs %s%s)" % (l["team"], l.get("fp_pos_rank") or "?", l.get("opp") or "?",
                                                  ", opp implied %.0f pts" % l["opp_implied"] if l.get("opp_implied") else "") for l in plan["def_streams"]))
        else:
            L.append(c(GRN, " DEF: ") + "keep %s (%s this week per FantasyPros). No stream needed." % (md["team"], md.get("fp_pos_rank") or "ranked"))
    if plan["byes"]:
        L.append(c(DIM, " upcoming byes: ") + "; ".join("wk%d: %s" % (w, ", ".join("%s %s" % (l["pos"], l["name"].split()[-1] if l["pos"] != "DEF" else l["team"]) for l in ls))
                                                        for w, ls in sorted(plan["byes"].items())))
    if plan["trending"]:
        L.append(c(DIM, " league-wide hot pickups (managers adding in 48h, for awareness only): ") + ", ".join(
            "%s %s %s" % (l["pos"], short(l["name"], 16), c(DIM, "%dk" % (n // 1000) if n >= 1000 else str(n))) for l, n in plan["trending"][:6]))
        top, n = plan["trending"][0]
        if n >= 50000 and plan.get("cheapest_drop") and not plan["claims"]:
            d = plan["cheapest_drop"]
            L.append(c(DIM, "   if you want %s anyway, the drop that costs you least is %s %s (your lowest-value bench player)." % (top["name"].split()[-1], d["pos"], d["name"])))
            if plan.get("hot"):
                L.append(c(DIM, "   rivals: " + _rivals_line(plan["hot"]["block"], plan["hot"]["leak"], d)))
    return "\n".join(L)


def _rivals_line(block, leak, drop):
    """'blocking value: Tua +0.0, Andy +0.0 · your drop (Robinson) would help them: Tua +0.4, Andy +0.4'"""
    fmt = lambda pairs: ", ".join("%s %+.1f" % (short(o, 10), g) for o, g in pairs)
    bmax = max((g for _, g in block), default=0)
    lmax = max((g for _, g in leak), default=0)
    verdict = "no block value" if bmax < 1 else ("worth blocking" if bmax >= 4 else "minor block value")
    leak_v = "safe to drop" if lmax < 1 else ("careful: helps a rival" if lmax >= 4 else "small gift to a rival")
    return "if a top-2 rival grabs the pickup: %s (%s) · if they grab your drop %s: %s (%s)" % (
        fmt(block), verdict, drop["name"].split()[-1], fmt(leak), leak_v)


# ------------------------------------------------------------------ trades
def render_trades(ctx):
    L = [bold("TRADES — deadline week %d" % config.TRADE_DEADLINE_WEEK)]
    if ctx.week > config.TRADE_DEADLINE_WEEK:
        L.append(c(DIM, "  deadline passed"))
        return "\n".join(L)
    pend = pending_trades(ctx)
    for t in pend:
        L.append(c(B + YEL, "★ PENDING offer (transaction %s): " % t.get("transaction_id")) + _describe_txn(ctx, t))
    ideas, base = trade_ideas(ctx)
    if not ideas:
        L.append(c(GRN, "✔ No trade improves your lineup without gutting the other side. None to propose."))
        return "\n".join(L)
    L.append(c(B + YEL, "★ Offers worth sending (your gain / their gain, in ROS lineup value):"))
    for i, t in enumerate(ideas, 1):
        L.append("  %d. to %-16s give %s  for %s   %s" % (
            i, short(t["owner"], 16), c(RED, " + ".join("%s %s" % (e["pos"], e["name"].split()[-1]) for e in t["give"])),
            c(GRN, " + ".join("%s %s" % (e["pos"], e["name"].split()[-1]) for e in t["get"])),
            c(DIM, "you +%.1f / them %+.1f" % (t["my_gain"], t["their_gain"]))))
    return "\n".join(L)


def _describe_txn(ctx, t):
    adds = t.get("adds") or {}
    parts = []
    for pid, rid in adds.items():
        parts.append("%s -> %s" % (ctx.name(pid), ctx.owner_name(rid)))
    return "; ".join(parts) or "(details unavailable)"


def render_trade_eval(ctx, text):
    """`give A, B for C` or `A for C`."""
    if " for " not in text:
        return "format: give <my players> for <their players>"
    give, get = text.split(" for ", 1)
    give = give.replace("give", "", 1)
    gl = [x.strip() for x in give.replace(" and ", ",").split(",") if x.strip()]
    tl = [x.strip() for x in get.replace(" and ", ",").split(",") if x.strip()]
    r = evaluate_trade(ctx, gl, tl)
    if r.get("error"):
        return c(RED, r["error"])
    L = [bold("TRADE EVAL vs %s" % r["partner"])]
    L.append("  give: " + ", ".join("%s %s (%.0f)" % (e["pos"], e["name"], e["value"]) for e in r["give"]))
    L.append("  get:  " + ", ".join("%s %s (%.0f)" % (e["pos"], e["name"], e["value"]) for e in r["get"]))
    L.append("  your lineup %+.1f | their lineup %+.1f | raw value %.0f -> %.0f | roster size after: %d/15" % (
        r["my_gain"], r["their_gain"], r["value_give"], r["value_get"], r["roster_size_after"]))
    if r["my_gain"] >= 3:
        verdict = c(B + GRN, "ACCEPT")
    elif r["my_gain"] <= -3:
        verdict = c(B + RED, "DECLINE")
    else:
        verdict = c(B + YEL, "COIN FLIP — counter for a small add-on")
    L.append("  ★ " + verdict)
    return "\n".join(L)


# ------------------------------------------------------------------ recap
def render_recap(ctx):
    r = recap(ctx)
    if not r:
        return bold("RECAP") + "\n  season hasn't started"
    if r.get("empty"):
        return bold("RECAP — week %d" % r["week"]) + "\n  no scores posted yet"
    L = [bold("RECAP — week %d" % r["week"])]
    mine = next((g for g in r["games"] if g["mine"]), None)
    if mine:
        won = mine["winner"] == ctx.my_rid
        opp = mine["loser"] if won else mine["winner"]
        L.append("  %s vs %s  %.1f - %.1f" % (c(B + (GRN if won else RED), "WON" if won else "LOST"), ctx.owner_name(opp),
                                            r["scores"][ctx.my_rid], r["scores"][opp]))
    L.append(hr())
    L.append("  results: " + "; ".join("%s %.0f def %s %.0f" % (short(ctx.owner_name(g["winner"]), 12), g["w_pts"], short(ctx.owner_name(g["loser"]), 12), g["l_pts"]) for g in r["games"]))
    hi = max(r["scores"], key=r["scores"].get)
    lo = min(r["scores"], key=r["scores"].get)
    L.append("  high: %s %.1f   low: %s %.1f" % (ctx.owner_name(hi), r["scores"][hi], ctx.owner_name(lo), r["scores"][lo]))
    L.append("  top performers: " + ", ".join("%s %.1f (%s)" % (short(ctx.name(pid), 16), pts, short(ctx.owner_name(rid), 10)) for pts, pid, rid in r["top"]))
    if r["bottom"]:
        L.append("  duds: " + ", ".join("%s %.1f (%s)" % (short(ctx.name(pid), 16), pts, short(ctx.owner_name(rid), 10)) for pts, pid, rid in r["bottom"]))
    if r["bench_regret"]:
        L.append(c(YEL, "  your bench regret: ") + ", ".join("%s %.1f sat behind %s %.1f" % (ctx.name(a), ap, ctx.name(b).split()[-1], bp) for a, ap, b, bp in r["bench_regret"]))
    L.append(hr())
    L.append("  standings: " + ", ".join("%d. %s %d-%d" % (i, short(ctx.owner_name(x["roster_id"]), 12), x["settings"].get("wins", 0), x["settings"].get("losses", 0)) for i, x in enumerate(r["standings"], 1)))
    L.append(hr())
    L.append(bold("  trash talk (edit to taste):"))
    lo_name = ctx.owner_name(lo)
    hi_name = ctx.owner_name(hi)
    lines = ["%s put up %.0f. My kicker outscored half that roster." % (lo_name, r["scores"][lo])]
    if r["top"]:
        lines.append("%s got %.0f from %s and still had to sweat it out." % (ctx.owner_name(r["top"][0][2]), r["top"][0][0], ctx.name(r["top"][0][1]).split()[-1]))
    if mine and mine["winner"] == ctx.my_rid:
        lines.append("Week %d in the books. %s, thanks for the free win — see you in the consolation bracket." % (r["week"], ctx.owner_name(mine["loser"])))
    elif mine:
        lines.append("Fine, %s. Enjoy it. The projections say that was your one." % ctx.owner_name(mine["winner"]))
    else:
        lines.append("%s is leading the league with %.0f. Peaking in September is a bold strategy." % (hi_name, r["scores"][hi]))
    for t in lines[:3]:
        L.append("   • " + t)
    return "\n".join(L)


# ------------------------------------------------------------------ brief
def render_brief(ctx, sections=None):
    dow = datetime.now().weekday()   # Mon=0
    if not sections:
        if dow in (1,):            # Tuesday: recap + waivers + trades + preview
            sections = ["recap", "waivers", "trades", "preview"]
        elif dow == 2:             # Wednesday
            sections = ["waivers", "trades", "preview", "lineup"]
        else:                      # Thu-Mon: game-time
            sections = ["lineup", "preview"]
    out = [c(DIM, "%s · %s · week %d · %s" % (config.LEAGUE_NAME, time.strftime("%a %b %-d %H:%M"), ctx.week,
                                             "sections: " + ", ".join(sections)))]
    fns = {"lineup": render_lineup, "preview": render_preview, "waivers": render_waivers, "trades": render_trades, "recap": render_recap}
    for s in sections:
        out.append("")
        try:
            out.append(fns[s](ctx))
        except Exception as e:
            out.append(c(RED, "%s section failed: %s" % (s, e)))
    return "\n".join(out) + "\n"
