"""Build the ranked draft value board for this league's scoring and roster shape."""
import json
import math
import os
import time

from . import config
from .scoring import league_points
from .util import norm_name, norm_team, warn

# Weight of projection-based VORP vs consensus-implied value in the final number.
W_PROJ = 0.5
W_ECR = 0.5

# FantasyPros nickname -> Sleeper legal name (normalized)
NAME_ALIASES = {"hollywoodbrown": "marquisebrown", "scottymiller": "scottmiller"}


def _player_index(players):
    """(normalized name, pos) -> player_id and name -> [player_ids], active only."""
    by_name_pos, by_name = {}, {}
    for pid, p in players.items():
        pos = p.get("position")
        if pos == "FB":
            pos = "RB"
        if pos not in config.POSITIONS:
            continue
        n = norm_name("%s %s" % (p.get("first_name") or "", p.get("last_name") or ""))
        if not n:
            continue
        # prefer players on a team when names collide
        key = (n, pos)
        if key not in by_name_pos or (p.get("team") and not players[by_name_pos[key]].get("team")):
            by_name_pos[key] = pid
        by_name.setdefault(n, []).append(pid)
    return by_name_pos, by_name


def _match_fp(fp_row, players, by_name_pos, by_name):
    pos = (fp_row.get("player_position_id") or "").upper()
    team = norm_team(fp_row.get("player_team_id"))
    if pos == "DST":
        return team if team in players else None
    n = norm_name(fp_row.get("player_name") or "")
    n = NAME_ALIASES.get(n, n)
    pid = by_name_pos.get((n, pos))
    if pid:
        return pid
    cands = by_name.get(n) or []
    for c in cands:
        if players[c].get("team") == team:
            return c
    return cands[0] if len(cands) == 1 else None


def build_board(league, players, projections, fp_rows, byes, manual_rows=None):
    scoring = league.get("scoring_settings") or {}
    if not scoring:
        warn("no scoring_settings in league data; falling back to Sleeper half-PPR points")
    entries = {}

    # 1. Sleeper season projections -> league-scored points + ADP
    for p in projections or []:
        pid = str(p.get("player_id"))
        st = p.get("stats") or {}
        pl = players.get(pid) or p.get("player") or {}
        pos = pl.get("position") or (p.get("player") or {}).get("position")
        if pos == "FB":
            pos = "RB"
        if pid in players and players[pid].get("position") == "DEF":
            pos = "DEF"
        if pos not in config.POSITIONS:
            continue
        team = pl.get("team") or p.get("team")
        adp = st.get("adp_half_ppr")
        adp = float(adp) if adp and float(adp) < 999 else None
        pts = league_points(st, scoring, pos) if scoring else float(st.get("pts_half_ppr") or 0)
        if pts <= 0 and adp is None:
            continue
        name = pl.get("full_name") or ("%s %s" % (pl.get("first_name", ""), pl.get("last_name", ""))).strip()
        if pos == "DEF" and pid in players:
            name = "%s %s" % (players[pid].get("first_name", ""), players[pid].get("last_name", ""))
        entries[pid] = {
            "player_id": pid, "name": name.strip(), "pos": pos, "team": team,
            "pts": round(pts, 1), "adp": adp, "sleeper_pts": st.get("pts_half_ppr"),
            "ecr": None, "ecr_tier": None, "ecr_pos_rank": None, "ecr_std": None,
            "bye": byes.get(team) if team else None,
            "injury": (players.get(pid) or {}).get("injury_status"),
            "age": (players.get(pid) or {}).get("age"),
        }

    # 2. FantasyPros ECR merge
    by_name_pos, by_name = _player_index(players)
    unmatched = 0
    for row in fp_rows or []:
        pid = _match_fp(row, players, by_name_pos, by_name)
        if not pid:
            unmatched += 1
            continue
        if pid not in entries:
            pl = players[pid]
            pos = pl.get("position")
            if pos == "FB":
                pos = "RB"
            if pos not in config.POSITIONS:
                continue
            entries[pid] = {
                "player_id": pid, "name": pl.get("full_name") or row.get("player_name"),
                "pos": pos, "team": pl.get("team"), "pts": 0.0, "adp": None, "sleeper_pts": None,
                "ecr": None, "ecr_tier": None, "ecr_pos_rank": None, "ecr_std": None,
                "bye": byes.get(pl.get("team")), "injury": pl.get("injury_status"), "age": pl.get("age"),
            }
        e = entries[pid]
        e["ecr"] = int(row.get("rank_ecr") or 0) or None
        e["ecr_tier"] = row.get("tier")
        e["ecr_pos_rank"] = row.get("pos_rank")
        try:
            e["ecr_std"] = float(row.get("rank_std") or 0)
        except ValueError:
            pass
        if not e.get("bye") and row.get("player_bye_week"):
            try:
                e["bye"] = int(row["player_bye_week"])
            except ValueError:
                pass
    if fp_rows and unmatched:
        warn("FantasyPros: %d of %d rows could not be matched to Sleeper ids" % (unmatched, len(fp_rows)))

    # 3. manual CSV (optional): overrides ECR rank
    for row in manual_rows or []:
        n = norm_name(row["name"])
        pid = by_name_pos.get((n, (row.get("pos") or "").upper())) or (by_name.get(n) or [None])[0]
        if pid and pid in entries:
            try:
                entries[pid]["ecr"] = int(row["rank"])
                if row.get("tier"):
                    entries[pid]["ecr_tier"] = int(row["tier"])
            except ValueError:
                pass

    rows = list(entries.values())

    # 4. VORP per position
    for pos in config.POSITIONS:
        group = sorted([r for r in rows if r["pos"] == pos], key=lambda r: -r["pts"])
        n = config.REPLACEMENT_RANK[pos]
        repl = group[n - 1]["pts"] if len(group) >= n else (group[-1]["pts"] if group else 0)
        for r in group:
            r["repl"] = repl
            r["vorp"] = round(r["pts"] - repl, 1) if r["pts"] > 0 else None

    # 5. consensus-implied value: VORP of the player sitting at overall rank == ECR
    curve = sorted([r["vorp"] for r in rows if r["vorp"] is not None], reverse=True)

    def curve_at(rank):
        if not curve:
            return 0.0
        i = max(0, min(len(curve) - 1, int(rank) - 1))
        return curve[i]

    for r in rows:
        v_proj, ecr = r["vorp"], r["ecr"]
        if r["pos"] in ("K", "DEF") and v_proj is not None:
            r["value"] = round(v_proj, 1)      # consensus ranks K/DEF ~150-200 overall; VORP alone orders them
        elif v_proj is not None and ecr:
            r["value"] = round(W_PROJ * v_proj + W_ECR * curve_at(ecr), 1)
        elif v_proj is not None:
            r["value"] = round(v_proj, 1)
        elif ecr:
            r["value"] = round(curve_at(ecr) * 0.9, 1)   # unprojected but ranked: light haircut
        else:
            r["value"] = -999.0

    rows = [r for r in rows if r["value"] > -999]
    rows.sort(key=lambda r: (-r["value"], r["adp"] or 999, r["ecr"] or 9999))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    # 6. positional rank + gap-based tiers on value
    for pos in config.POSITIONS:
        group = [r for r in rows if r["pos"] == pos]
        tier, prev = 1, None
        for i, r in enumerate(group, 1):
            r["pos_rank"] = i
            if prev is not None and prev > 0:
                gap = prev - r["value"]
                if pos in ("RB", "WR"):
                    thresh = max(6.0, 0.10 * prev)
                elif pos in ("QB", "TE"):
                    thresh = max(6.0, 0.12 * prev)
                else:
                    thresh = max(2.0, 0.12 * prev)
                if gap >= thresh:
                    tier += 1
            r["tier"] = tier
            prev = r["value"]

    return rows


def write_board(rows, meta, data_dir=config.DATA_DIR):
    path_json = os.path.join(data_dir, "board.json")
    with open(path_json, "w") as f:
        json.dump({"built_at": time.time(), "meta": meta, "players": rows}, f)
    with open(os.path.join(data_dir, "board.txt"), "w") as f:
        f.write(render_board_text(rows, meta))
    return path_json


def load_board(data_dir=config.DATA_DIR):
    path = os.path.join(data_dir, "board.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def fmt_row(r, width_name=24):
    adp = ("%5.1f" % r["adp"]) if r.get("adp") else "   --"
    ecr = ("%4d" % r["ecr"]) if r.get("ecr") else "  --"
    inj = (" [%s]" % r["injury"][:3].upper()) if r.get("injury") else ""
    bye = ("%2s" % r["bye"]) if r.get("bye") else "--"
    return "%3d %-3s%-3s %-*s %-4s bye%s %6.1f %6.1f %6.1f  adp%s ecr%s T%d%s" % (
        r["rank"], r["pos"], r.get("pos_rank", 0), width_name, r["name"][:width_name], r.get("team") or "FA",
        bye, r["pts"], r.get("vorp") or 0, r["value"], adp, ecr, r.get("tier", 0), inj)


def render_board_text(rows, meta, top=220):
    out = []
    out.append("%s draft board — built %s" % (config.LEAGUE_NAME, time.strftime("%Y-%m-%d %H:%M")))
    out.append("scoring: half-PPR (rec 0.5), pass_yd 0.04, pass_td 4, int -1, fum_lost -2; 12 teams; QB/2RB/2WR/TE/FLEX/K/DEF + 6 BN")
    out.append("value = %.2f*VORP(league-scored projection) + %.2f*consensus-implied VORP (FantasyPros ECR)" % (W_PROJ, W_ECR))
    out.append("sources: " + "; ".join("%s: %s" % (k, v) for k, v in meta.get("sources", {}).items()))
    out.append("")
    out.append("=== OVERALL (top %d) ===" % top)
    out.append("rnk pos     name                     team bye    pts   vorp  value    adp  ecr tier")
    for r in rows[:top]:
        out.append(fmt_row(r))
    out.append("")
    out.append("=== TIER BREAK TIMING (by ADP; 'gone by' = latest ADP in the tier, i.e. expect the tier to be empty around that pick) ===")
    for pos in config.POSITIONS:
        group = [r for r in rows if r["pos"] == pos]
        for t in range(1, 7):
            g = [r for r in group if r["tier"] == t]
            if not g or g[0]["value"] <= 0:
                break
            adps = [r["adp"] for r in g if r.get("adp")]
            names = ", ".join(r["name"].split()[-1] if r["pos"] != "DEF" else r["team"] for r in g[:8]) + (" …" if len(g) > 8 else "")
            timing = ("ADP %.0f-%.0f -> gone by ~pick %.0f (round %d)" % (
                min(adps), max(adps), max(adps), (max(adps) - 1) // config.NUM_TEAMS + 1)) if adps else "no ADP"
            out.append("  %-3s T%d (%2d): %-70s %s" % (pos, t, len(g), names[:70], timing))
    for pos in config.POSITIONS:
        group = [r for r in rows if r["pos"] == pos]
        limit = {"QB": 28, "RB": 60, "WR": 70, "TE": 24, "K": 16, "DEF": 16}[pos]
        out.append("")
        out.append("=== %s (top %d) — tiers break where value gaps open ===" % (pos, limit))
        cur = None
        for r in group[:limit]:
            if r["tier"] != cur:
                cur = r["tier"]
                n_in = sum(1 for x in group if x["tier"] == cur)
                out.append("  -- Tier %d (%d players) --" % (cur, n_in))
            out.append(fmt_row(r))
    return "\n".join(out) + "\n"
