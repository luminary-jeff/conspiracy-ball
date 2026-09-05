"""Live draft state, recommendation engine, poll loop, and dry-run simulator.

Everything here works off the precomputed board (data/board.json). The only
network activity during a live draft is polling /draft/<id>/picks (and the draft
metadata every ~30s until the order is known). No heavy computation on the clock.
"""
import math
import random
import sys
import time
from collections import Counter

from . import config
from .sleeper import Sleeper
from .util import warn

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}


# ---------------------------------------------------------------- snake math
def snake_slot(pick_no, teams=config.NUM_TEAMS):
    """Return (slot, round) that owns overall pick number `pick_no` (1-based)."""
    rnd = (pick_no - 1) // teams + 1
    idx = (pick_no - 1) % teams
    slot = idx + 1 if rnd % 2 == 1 else teams - idx
    return slot, rnd


def slot_pick_numbers(slot, teams=config.NUM_TEAMS, rounds=config.ROUNDS):
    return [n for n in range(1, teams * rounds + 1) if snake_slot(n, teams)[0] == slot]


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------- state
class DraftState:
    def __init__(self, board_players, teams=config.NUM_TEAMS, rounds=config.ROUNDS,
                 my_slot=None, user_names=None, players_map=None):
        self.board = board_players                       # sorted by value desc
        self.by_id = {p["player_id"]: p for p in board_players}
        self.teams, self.rounds = teams, rounds
        self.my_slot = my_slot
        self.user_names = user_names or {}               # user_id -> display name
        self.slot_names = {}                             # slot -> display name (learned from picks)
        self.players_map = players_map or {}
        self.picks = []
        self.drafted = set()
        self.rosters = {s: [] for s in range(1, teams + 1)}
        self.taken_nos = set()
        self.status = "pre_draft"
        self.last_poll_ok = None
        self.last_error = None

    def set_picks(self, raw):
        picks = []
        for p in raw or []:
            pid = str(p.get("player_id") or "")
            if not pid:
                continue
            md = p.get("metadata") or {}
            b = self.by_id.get(pid)
            pm = self.players_map.get(pid) or {}
            name = (b and b["name"]) or pm.get("full_name") or ("%s %s" % (md.get("first_name", ""), md.get("last_name", ""))).strip() or pid
            pos = (b and b["pos"]) or pm.get("position") or md.get("position") or "?"
            if pos == "FB":
                pos = "RB"
            team = (b and b["team"]) or pm.get("team") or md.get("team")
            pick_no = int(p.get("pick_no") or 0)
            slot = int(p.get("draft_slot") or (snake_slot(pick_no, self.teams)[0] if pick_no else 0))
            picks.append({"pick_no": pick_no, "slot": slot, "round": int(p.get("round") or snake_slot(pick_no, self.teams)[1]),
                          "player_id": pid, "name": name, "pos": pos, "team": team,
                          "is_keeper": bool(p.get("is_keeper")), "picked_by": p.get("picked_by"),
                          "value": (b and b["value"]) or None})
            if p.get("picked_by") and slot:
                nm = self.user_names.get(p["picked_by"])
                if nm:
                    self.slot_names[slot] = nm
        picks.sort(key=lambda x: x["pick_no"])
        self.picks = picks
        self.drafted = {p["player_id"] for p in picks}
        self.taken_nos = {p["pick_no"] for p in picks}
        self.rosters = {s: [] for s in range(1, self.teams + 1)}
        for p in picks:
            if p["slot"] in self.rosters:
                self.rosters[p["slot"]].append(p)

    # -- pick bookkeeping
    @property
    def total_picks(self):
        return self.teams * self.rounds

    @property
    def current_pick_no(self):
        for n in range(1, self.total_picks + 1):
            if n not in self.taken_nos:
                return n
        return self.total_picks + 1

    @property
    def is_complete(self):
        return self.status == "complete" or self.current_pick_no > self.total_picks

    def on_clock_slot(self):
        n = self.current_pick_no
        return snake_slot(n, self.teams)[0] if n <= self.total_picks else None

    def my_pick_numbers(self):
        if not self.my_slot:
            return []
        return [n for n in slot_pick_numbers(self.my_slot, self.teams, self.rounds) if n not in self.taken_nos]

    def my_next_picks(self):
        """(next, following) overall pick numbers still owed to me (None if none)."""
        nos = self.my_pick_numbers()
        return (nos[0] if nos else None, nos[1] if len(nos) > 1 else None)

    def my_roster(self):
        return self.rosters.get(self.my_slot, []) if self.my_slot else []

    def slot_name(self, slot):
        if slot == self.my_slot:
            return "YOU"
        return self.slot_names.get(slot) or ("Team %d" % slot)

    def available(self):
        return [p for p in self.board if p["player_id"] not in self.drafted]

    def recent_picks(self, n=6):
        return self.picks[-n:]

    def run_alert(self, n=6):
        rec = self.recent_picks(n)
        if len(rec) < 4:
            return None
        c = Counter(p["pos"] for p in rec)
        pos, k = c.most_common(1)[0]
        if k >= 3 and pos in ("QB", "RB", "WR", "TE"):
            return "RUN: %d of last %d picks were %s" % (k, len(rec), pos)
        return None


# ---------------------------------------------------------------- availability
def effective_adp(p, current_pick):
    """ADP proxy, floored at the current pick (a faller is re-priced to 'now')."""
    a = p.get("adp") or p.get("ecr") or (p.get("rank", 300) * 1.25)
    return max(float(a), current_pick - 0.5)


def p_available(p, at_pick, current_pick):
    """P(player still on the board when `at_pick` comes up), from an ADP-centred normal."""
    if at_pick is None or at_pick <= current_pick:
        return 1.0
    adp = effective_adp(p, current_pick)
    sd = 3.0 + 0.16 * adp
    return 1.0 - _phi((at_pick - 0.5 - adp) / sd)


# ---------------------------------------------------------------- needs
def need_multiplier(pos, counts, picks_left, rnd):
    """How much of a player's value I actually capture given my roster so far.
    Starters count in full; bench depth only pays off on byes/injuries."""
    c = counts.get(pos, 0)
    if pos in ("K", "DEF"):
        # house rule: K/DEF only in the last three rounds unless the board is empty
        if c >= 1:
            return 0.03
        if picks_left <= 2:
            return 2.0
        if picks_left <= 3:
            return 1.0
        return 0.05
    if c < STARTERS[pos]:
        return 1.0
    extras = sum(max(0, counts.get(q, 0) - STARTERS[q]) for q in config.FLEX_ELIGIBLE)
    if pos in config.FLEX_ELIGIBLE and extras < 1:
        return 0.9
    target = config.ROSTER_TARGET[pos]
    if pos in ("QB", "TE"):
        if c >= target:
            return 0.02
        return 0.22 if rnd >= 9 else 0.1
    # RB / WR depth
    if c < 4:
        return 0.6
    if c < 5:
        return 0.5
    if c < target:
        return 0.4
    return 0.2


def _tier_left(avail_by_pos, pos, tier):
    return sum(1 for p in avail_by_pos.get(pos, []) if p["tier"] == tier)


# ---------------------------------------------------------------- lineup objective (dynamic VBD)
BENCH_W = {"RB": 0.15, "WR": 0.15, "TE": 0.05, "QB": 0.05}   # bench = insurance; fraction of value credited
MAX_AT = {"QB": 2, "TE": 2, "K": 1, "DEF": 1}                  # never draft more than this


def bpts(p):
    """Blended projected season points (value + replacement baseline)."""
    return p["value"] + (p.get("repl") or 0.0)


def lineup_value(players):
    """Projected value of the best legal starting lineup + small bench credit.
    Each starter counts (blended pts - replacement at his position); empty slots count 0
    (i.e. a waiver-level fill). This is the quantity the draft strategy maximizes."""
    by = {}
    for p in players:
        by.setdefault(p["pos"], []).append(p["value"])
    for v in by.values():
        v.sort(reverse=True)
    total = 0.0
    bench = []
    for pos, n in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("K", 1), ("DEF", 1)):
        v = by.get(pos, [])
        total += sum(x for x in v[:n] if x > 0)
        bench.extend((pos, x) for x in v[n:])
    flex = [(x, pos) for pos, x in bench if pos in config.FLEX_ELIGIBLE]
    if flex:
        fx = max(flex)
        if fx[0] > 0:
            total += fx[0]
        bench.remove((fx[1], fx[0]))
    total += sum(BENCH_W.get(pos, 0.0) * x for pos, x in bench if x > 0)
    return total


def complete_roster(roster, future_picks, pool, cur, picks_left_after):
    """Greedy expected completion: at each of my future picks take the player with the
    best (marginal lineup gain x P(still available)). Returns final lineup_value."""
    roster = list(roster)
    counts = Counter(p["pos"] for p in roster)
    taken = set()
    base = lineup_value(roster)
    n_future = len(future_picks)
    for i, k in enumerate(future_picks):
        left_after_this = n_future - i - 1
        best, best_gain = None, 0.0
        for q in pool:
            pid = q["player_id"]
            if pid in taken:
                continue
            pos = q["pos"]
            if counts.get(pos, 0) >= MAX_AT.get(pos, 99):
                continue
            if pos in ("K", "DEF") and left_after_this >= 2:
                continue
            pa = p_available(q, k, cur)
            if pa < 0.05:
                continue
            gain = (lineup_value(roster + [q]) - base) * pa
            if gain > best_gain:
                best, best_gain = q, gain
        if best is None:
            continue
        roster.append(best)
        taken.add(best["player_id"])
        counts[best["pos"]] += 1
        base = lineup_value(roster)
    return base, roster


# ---------------------------------------------------------------- recommendation
def recommend(state, n_alt=4):
    """Return dict with 'pick' (board entry), 'reason', 'alts', 'need_lines', 'gone' ...
    or None when I have no picks left / slot unknown."""
    avail = state.available()
    if not avail:
        return None
    roster = state.my_roster()
    counts = Counter(p["pos"] for p in roster)
    picks_left = state.rounds - len(roster)
    cur = state.current_pick_no
    nxt, following = state.my_next_picks()
    on_clock = state.my_slot is not None and nxt == cur and state.status not in ("pre_draft",)
    rnd = snake_slot(nxt, state.teams)[1] if nxt else snake_slot(cur, state.teams)[1]
    avail_by_pos = {}
    for p in avail:
        avail_by_pos.setdefault(p["pos"], []).append(p)

    # hard constraints: enough picks must remain to fill every starting slot
    unfilled = {pos: STARTERS[pos] - counts.get(pos, 0) for pos in STARTERS if counts.get(pos, 0) < STARTERS[pos]}
    must_positions = None
    if picks_left <= sum(unfilled.values()):
        must_positions = set(unfilled)

    def scored(pl, cnts):
        m = need_multiplier(pl["pos"], cnts, picks_left, rnd)
        base = max(pl["value"], 0.0) + 0.005 * (pl.get("pts") or 0.0)
        return base * m

    roster_entries = [state.by_id[p["player_id"]] for p in roster if p["player_id"] in state.by_id]
    future = [n for n in state.my_pick_numbers() if n != nxt]      # picks after the one being decided
    pool = [p for p in avail[:220] if p["pos"] not in ("K", "DEF")] + \
           [p for p in avail if p["pos"] == "K"][:6] + [p for p in avail if p["pos"] == "DEF"][:6]

    # candidate set: top by the fast need heuristic + top by raw value, deduped, legal
    cands = []
    seen = set()
    fast = sorted(avail[:250], key=lambda p: -scored(p, counts))
    for p in fast[:18] + avail[:12]:
        pid = p["player_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if must_positions and p["pos"] not in must_positions:
            continue
        if counts.get(p["pos"], 0) >= MAX_AT.get(p["pos"], 99):
            continue
        if p["pos"] in ("K", "DEF") and picks_left > 3 and not must_positions:
            continue
        cands.append(p)
    if not cands:
        return None

    # dynamic VBD: score = projected lineup value after completing the roster through all my picks
    ranked = []
    for p in cands:
        p_reach = p_available(p, nxt, cur) if not on_clock else 1.0
        final_val, plan = complete_roster(roster_entries + [p], future, [q for q in pool if q["player_id"] != p["player_id"]],
                                          cur, picks_left - 1)
        p_wait = p_available(p, following, cur) if following else 0.0
        ranked.append({"player": p, "score": final_val, "now": p["value"], "p_reach": p_reach, "p_wait": p_wait,
                       "plan": plan[len(roster_entries) + 1:]})
    ranked.sort(key=lambda i: (-i["score"], -i["player"]["value"]))
    pick = ranked[0]
    alts = ranked[1:1 + n_alt]
    for a in alts:
        a["margin"] = pick["score"] - a["score"]
    reason = _reason(pick, counts, avail_by_pos, nxt, following, on_clock, picks_left, rnd)
    if pick.get("plan"):
        plan_bits = []
        for k, q in zip(future, pick["plan"]):
            plan_bits.append("%s@%d" % (q["pos"], k))
        pick["plan_str"] = " ".join(plan_bits[:6])

    # per-position best available (for the "need" panel)
    need_lines = []
    for pos in config.POSITIONS:
        g = avail_by_pos.get(pos, [])[:3]
        if not g:
            continue
        t1 = g[0]["tier"]
        left = _tier_left(avail_by_pos, pos, t1)
        need_lines.append({"pos": pos, "have": counts.get(pos, 0), "need": STARTERS[pos] - counts.get(pos, 0),
                           "players": g, "tier": t1, "tier_left": left,
                           "mult": need_multiplier(pos, counts, picks_left, rnd)})

    # likely gone before my next turn (when not on the clock) / before the following pick
    horizon = following if on_clock else nxt
    gone = []
    if horizon:
        for p in avail[:40]:
            pa = p_available(p, horizon, cur)
            if pa < 0.5:
                gone.append((p, pa))
    return {"pick": pick, "reason": reason, "alts": alts, "need_lines": need_lines, "gone": gone[:10],
            "on_clock": on_clock, "next": nxt, "following": following, "picks_left": picks_left,
            "round": rnd, "must": must_positions}


def _slot_label(pos, counts):
    c = counts.get(pos, 0)
    if pos in ("K", "DEF"):
        return "fills %s" % pos if c == 0 else "%s #%d" % (pos, c + 1)
    if c < STARTERS[pos]:
        return "%s%d open" % (pos, c + 1)
    if pos in config.FLEX_ELIGIBLE:
        extras = sum(max(0, counts.get(q, 0) - STARTERS[q]) for q in config.FLEX_ELIGIBLE)
        if extras < 1:
            return "fills FLEX"
    return "%s depth (#%d)" % (pos, c + 1)


def _reason(item, counts, avail_by_pos, nxt, following, on_clock, picks_left, rnd):
    p = item["player"]
    bits = [_slot_label(p["pos"], counts)]
    left = _tier_left(avail_by_pos, p["pos"], p["tier"])
    if left <= 1:
        bits.append("LAST in %s tier %d" % (p["pos"], p["tier"]))
    elif left <= 3:
        bits.append("%d left in %s tier %d" % (left, p["pos"], p["tier"]))
    else:
        bits.append("%s tier %d" % (p["pos"], p["tier"]))
    adp = ("ADP %.0f" % p["adp"]) if p.get("adp") else "no ADP"
    if following:
        pw = item["p_wait"]
        if pw < 0.35:
            bits.append("%s, gone by #%d (%d%%)" % (adp, following, round(pw * 100)))
        elif pw > 0.7:
            bits.append("%s, likely there at #%d (%d%%) but nothing better now" % (adp, following, round(pw * 100)))
        else:
            bits.append("%s, coin-flip to reach #%d (%d%%)" % (adp, following, round(pw * 100)))
    else:
        bits.append(adp)
    if not on_clock and item["p_reach"] < 0.6 and nxt:
        bits.append("only %d%% to reach your #%d" % (round(item["p_reach"] * 100), nxt))
    if p.get("injury"):
        bits.append("injury: %s" % p["injury"])
    return " | ".join(bits)


# ---------------------------------------------------------------- live loop
def resolve_my_slot(draft, state):
    order = draft.get("draft_order") or {}
    s = order.get(config.MY_USER_ID)
    if s:
        return int(s)
    for p in state.picks:
        if p.get("picked_by") == config.MY_USER_ID and p.get("slot"):
            return p["slot"]
    return None


def run_live(board, slot=None, interval=5.0, once=False):
    from .ui import render
    api = Sleeper()
    users = api.users() or []
    names = {u["user_id"]: (u.get("display_name") or u.get("username") or u["user_id"]) for u in users}
    draft = api.draft(live=True) or {}
    settings = draft.get("settings") or {}
    teams = int(settings.get("teams") or config.NUM_TEAMS)
    rounds = int(settings.get("rounds") or config.ROUNDS)
    state = DraftState(board["players"], teams=teams, rounds=rounds, my_slot=slot, user_names=names)
    state.status = draft.get("status") or "pre_draft"
    # pre-seed slot names from draft_order if present
    for uid, s in (draft.get("draft_order") or {}).items():
        state.slot_names[int(s)] = names.get(uid, uid)
    if not state.my_slot:
        state.my_slot = resolve_my_slot(draft, state)
    last_meta = time.time()
    board_age = time.time() - board.get("built_at", 0)
    while True:
        try:
            now = time.time()
            if (not state.my_slot or state.status != "complete") and now - last_meta >= 30:
                d = api.draft(live=True)
                if d:
                    draft = d
                    state.status = d.get("status") or state.status
                    for uid, s in (d.get("draft_order") or {}).items():
                        state.slot_names[int(s)] = names.get(uid, uid)
                    if not slot:
                        state.my_slot = resolve_my_slot(d, state) or state.my_slot
                last_meta = now
            picks = api.draft_picks()
            if picks is not None:
                state.set_picks(picks)
                state.last_poll_ok = time.time()
                state.last_error = None
                if not state.my_slot:
                    state.my_slot = resolve_my_slot(draft, state)
            else:
                state.last_error = "poll failed; showing last known state"
            rec = recommend(state) if state.my_slot else None
            sys.stdout.write(render(state, rec, board_age=board_age, mode="LIVE"))
            sys.stdout.flush()
            if once or state.is_complete:
                break
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nbye.")
            return 0
    return 0


# ---------------------------------------------------------------- dry run
def _sim_other_pick(state, slot, rnd, rng):
    roster = state.rosters[slot]
    counts = Counter(p["pos"] for p in roster)
    avail = state.available()
    need_kd = [pos for pos in ("K", "DEF") if counts.get(pos, 0) == 0]
    if rnd >= state.rounds - 1 and need_kd:
        pos = need_kd[0]
        g = [p for p in avail if p["pos"] == pos]
        if g:
            return g[0]
    pool = []
    for p in sorted(avail, key=lambda p: (p.get("adp") or p.get("ecr") or 999)):
        if p["pos"] in ("K", "DEF") and rnd < 13:
            continue
        if p["pos"] == "QB" and counts.get("QB", 0) >= 2:
            continue
        if p["pos"] == "TE" and counts.get("TE", 0) >= 2:
            continue
        if p["pos"] in ("RB", "WR") and counts.get(p["pos"], 0) >= 7:
            continue
        pool.append(p)
        if len(pool) >= 7:
            break
    if not pool:
        return avail[0]
    weights = [0.38, 0.24, 0.15, 0.10, 0.07, 0.04, 0.02][:len(pool)]
    return rng.choices(pool, weights=weights, k=1)[0]


def run_dry_run(board, slot=None, speed=0.35, my_pause=3.0, seed=None):
    from .ui import render, bold
    rng = random.Random(seed)
    my_slot = slot or rng.randint(1, config.NUM_TEAMS)
    fake_names = ["PyramidPower", "samubatt", "BigDogBF", "Wizardof365", "bennyboo86", "TuaLegit",
                  "AndyReidStach", "PSCoop", "BigfootShotJFK", "Haaland", "Team 11", "Team 12"]
    state = DraftState(board["players"], my_slot=my_slot)
    state.status = "drafting"
    for s in range(1, config.NUM_TEAMS + 1):
        state.slot_names[s] = fake_names[(s - 1) % len(fake_names)]
    picks = []
    t0 = time.time()
    try:
        for pick_no in range(1, state.total_picks + 1):
            s, rnd = snake_slot(pick_no)
            state.set_picks(picks)
            state.last_poll_ok = time.time()
            if s == my_slot:
                rec = recommend(state)
                sys.stdout.write(render(state, rec, board_age=0, mode="DRY RUN"))
                sys.stdout.flush()
                time.sleep(my_pause)
                choice = rec["pick"]["player"] if rec else state.available()[0]
            else:
                choice = _sim_other_pick(state, s, rnd, rng)
                if pick_no % 2 == 0 or rnd >= 13:
                    rec = recommend(state)
                    sys.stdout.write(render(state, rec, board_age=0, mode="DRY RUN"))
                    sys.stdout.flush()
                time.sleep(speed)
            picks.append({"pick_no": pick_no, "round": rnd, "draft_slot": s, "player_id": choice["player_id"],
                          "picked_by": config.MY_USER_ID if s == my_slot else "sim-%d" % s, "is_keeper": False,
                          "metadata": {"position": choice["pos"], "team": choice["team"]}})
        state.set_picks(picks)
        state.status = "complete"
        rec = None
        sys.stdout.write(render(state, rec, board_age=0, mode="DRY RUN"))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 0
    mine = state.my_roster()
    print(bold("\nDRY RUN COMPLETE in %.0fs — your simulated roster (slot %d):" % (time.time() - t0, my_slot)))
    for p in mine:
        b = state.by_id.get(p["player_id"], {})
        print("  R%-2d #%-3d %-3s %-24s %-4s val %6.1f  adp %s" % (
            p["round"], p["pick_no"], p["pos"], p["name"], p["team"] or "", b.get("value", 0),
            ("%.0f" % b["adp"]) if b.get("adp") else "--"))
    tot = sum((state.by_id.get(p["player_id"], {}).get("pts") or 0) for p in mine)
    print("  projected season points (all 15): %.0f" % tot)
    return 0
