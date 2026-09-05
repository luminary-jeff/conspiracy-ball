"""Terminal rendering for the draft screen. Pure string building; no I/O besides return."""
import os
import time

from . import config
from .util import age_str

CLR = "\x1b[2J\x1b[H"
B, DIM, RED, GRN, YEL, CYN, MAG, RST = "\x1b[1m", "\x1b[2m", "\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[35m", "\x1b[0m"
POS_COLOR = {"QB": MAG, "RB": GRN, "WR": CYN, "TE": YEL, "K": DIM, "DEF": DIM}
WIDTH = 100
USE_COLOR = os.environ.get("NO_COLOR") is None


def c(code, s):
    return "%s%s%s" % (code, s, RST) if USE_COLOR else s


def bold(s):
    return c(B, s)


def pos_c(pos):
    return c(POS_COLOR.get(pos, ""), "%-3s" % pos)


def hr(ch="─"):
    return c(DIM, ch * WIDTH)


def short(name, n=20):
    return name if len(name) <= n else name[:n - 1] + "…"


def _roster_line(roster):
    """QB: Allen  RB: Gibbs, Henry  WR: Chase, --  TE: --  FLEX: --  K: --  DEF: --  BN: 2/6"""
    by = {}
    for p in roster:
        by.setdefault(p["pos"], []).append(p["name"].split()[-1] if p["pos"] != "DEF" else p["team"])
    out = []
    used = {k: 0 for k in by}
    for slot in ("QB", "RB", "RB", "WR", "WR", "TE"):
        lst = by.get(slot, [])
        i = used.get(slot, 0)
        out.append((slot, lst[i] if i < len(lst) else None))
        used[slot] = i + 1
    flex = None
    for pos in config.FLEX_ELIGIBLE:
        lst = by.get(pos, [])
        i = used.get(pos, 0)
        if i < len(lst):
            flex = lst[i]
            used[pos] = i + 1
            break
    out.append(("FLEX", flex))
    for slot in ("K", "DEF"):
        lst = by.get(slot, [])
        out.append((slot, lst[0] if lst else None))
        used[slot] = 1 if lst else 0
    bench = sum(max(0, len(v) - used.get(k, 0)) for k, v in by.items())
    parts = []
    prev = None
    for slot, nm in out:
        label = "" if slot == prev else "%s:" % slot
        prev = slot
        cell = c(GRN, nm) if nm else c(RED, "--")
        parts.append("%s%s" % (c(DIM, label) if label else "", cell))
    parts.append("%s%d/%d" % (c(DIM, "BN:"), bench, config.BENCH_SLOTS))
    return "  ".join(parts)


def render(state, rec, board_age=0, mode="LIVE"):
    L = []
    cur = state.current_pick_no
    slot_now = state.on_clock_slot()
    rnd, idx = ((cur - 1) // state.teams + 1, (cur - 1) % state.teams + 1) if cur <= state.total_picks else (state.rounds, state.teams)
    nxt, following = state.my_next_picks()
    on_clock = state.my_slot is not None and slot_now == state.my_slot and not state.is_complete and state.status != "pre_draft"

    # header
    polled = age_str(state.last_poll_ok) if state.last_poll_ok else "never"
    hdr = "%s  %s  Pick %s (R%d.%02d)  On clock: %s" % (
        bold("CONSPIRACY BALL"), c(DIM, mode), bold("#%d" % cur) if not state.is_complete else "done", rnd, idx,
        bold(state.slot_name(slot_now)) if slot_now else "-")
    tail = c(DIM, "poll %s" % polled)
    if state.last_error:
        tail = c(RED, state.last_error)
    L.append(hdr + "   " + tail)
    if state.my_slot:
        if nxt:
            away = nxt - cur
            L.append("Your slot %s  |  next pick %s (%s)  then #%s  |  picks left %d" % (
                bold(str(state.my_slot)), bold("#%d" % nxt),
                c(YEL, "%d picks away" % away) if away else c(RED, "NOW"),
                following or "-", state.rounds - len(state.my_roster())))
        else:
            L.append("Your slot %s  |  roster full" % bold(str(state.my_slot)))
    else:
        L.append(c(YEL, "Draft order not set yet — waiting for Sleeper (re-checking every 30s). Use --slot N to force."))
    if on_clock:
        L.append(c(B + RED, "▶▶▶  YOU ARE ON THE CLOCK  (%ds timer)  ◀◀◀" % config.PICK_TIMER))
    if state.is_complete:
        L.append(c(B + GRN, "DRAFT COMPLETE"))
    elif state.status == "pre_draft":
        L.append(c(YEL, "Draft has not started yet — waiting. Recommendation below is for your first pick."))
    elif state.status == "paused":
        L.append(c(YEL, "Draft is PAUSED"))
    run = state.run_alert()
    recent = state.recent_picks(4)
    if recent:
        rp = " · ".join("#%d %s %s%s" % (p["pick_no"], short(p["name"].split()[-1] if p["pos"] != "DEF" else p["team"], 12), p["pos"],
                                          c(DIM, "(%s)" % short(state.slot_name(p["slot"]), 8)))
                        for p in reversed(recent))
        L.append(c(DIM, "last: ") + rp)
    if run:
        L.append(c(B + YEL, "⚠ " + run))
    L.append(hr())

    # my roster
    if state.my_slot:
        L.append(bold("MY ROSTER  ") + _roster_line(state.my_roster()))
        L.append(hr())

    # recommendation
    if rec and rec.get("pick"):
        p = rec["pick"]["player"]
        tag = "PICK NOW" if rec["on_clock"] else "TARGET for #%s" % rec["next"]
        L.append("%s %s  %s  %s" % (
            c(B + YEL, "★ " + tag + ":"), c(B, "%s (%s, %s)" % (p["name"], p["pos"], p["team"] or "FA")),
            c(B, "val %.1f" % p["value"]), c(DIM, "bye %s" % (p.get("bye") or "?"))))
        L.append("   " + rec["reason"])
        if rec.get("must"):
            L.append("   " + c(RED, "must fill: %s" % ", ".join(sorted(rec["must"]))))
        alts = (rec.get("alts") or [])[:3]
        if alts:
            L.append(c(DIM, "   next best: ") + "  ·  ".join(
                "%s %s %s%s" % (short(a["player"]["name"], 18), a["player"]["pos"],
                                c(DIM, "-%.1f" % a.get("margin", 0)),
                                c(DIM, " (%d%% at #%s)" % (round(a["p_wait"] * 100), rec["following"])) if rec["following"] else "")
                for a in alts))
        if rec["pick"].get("plan_str"):
            L.append(c(DIM, "   plan after this: ") + rec["pick"]["plan_str"] + c(DIM, "   (lineup value %.0f)" % rec["pick"]["score"]))
        L.append(hr())
    elif state.my_slot and not state.is_complete and not nxt:
        L.append(c(DIM, "no picks left for you"))
        L.append(hr())

    # top available
    avail = state.available()
    L.append(bold("TOP 10 AVAILABLE") + c(DIM, "   (value = league-scored VORP blended w/ consensus; T = tier)"))
    for p in avail[:10]:
        inj = c(RED, " %s" % p["injury"][:4].upper()) if p.get("injury") else ""
        adp = ("%5.1f" % p["adp"]) if p.get("adp") else "   --"
        ecr = ("%3d" % p["ecr"]) if p.get("ecr") else " --"
        L.append(" %3d %s %-22s %-4s bye%-3s %s  adp %s  ecr %s  T%d%s" % (
            p["rank"], pos_c(p["pos"]), short(p["name"], 22), p["team"] or "FA", p.get("bye") or "-",
            c(B, "%6.1f" % p["value"]), adp, ecr, p["tier"], inj))
    L.append(hr())

    # by position
    if rec and rec.get("need_lines"):
        L.append(bold("BEST BY POSITION") + c(DIM, "   have/need · tier-left flags"))
        for nl in rec["need_lines"]:
            flag = ""
            if nl["tier_left"] <= 1:
                flag = c(B + RED, " ⚠ last in T%d" % nl["tier"])
            elif nl["tier_left"] <= 3:
                flag = c(YEL, " ⚠ %d left in T%d" % (nl["tier_left"], nl["tier"]))
            need = c(RED, "need %d" % nl["need"]) if nl["need"] > 0 else c(DIM, "have %d" % nl["have"])
            players = "  ·  ".join("%s %s%s" % (short(q["name"], 18), c(B, "%.1f" % q["value"]), c(DIM, " T%d" % q["tier"]))
                                   for q in nl["players"])
            L.append(" %s %s  %s%s" % (pos_c(nl["pos"]), need, players, flag))
        L.append(hr())
        gone = rec.get("gone") or []
        horizon = rec["following"] if rec["on_clock"] else rec["next"]
        if gone and horizon:
            L.append(c(DIM, "likely gone before #%s: " % horizon) + ", ".join(
                "%s %s(%d%%)" % (short(g[0]["name"], 16), g[0]["pos"], round(g[1] * 100)) for g in gone[:6]))
    L.append(c(DIM, "board built %s · Ctrl-C to quit · this tool only recommends; make the pick in the Sleeper app" % age_str(time.time() - board_age)))
    return CLR + "\n".join(L) + "\n"
