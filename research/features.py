"""Pre-game features for every player-game. Everything here is knowable before kickoff: it is built
only from rows with an earlier (season, week) than the row being described, plus the closing line,
the venue/weather, and the announced starting quarterback.

Hypothesis groups (pre-registered in the plan, 2026-09-18):
  H1 vegas     implied team total, spread, game total
  H2 opponent  how the opposing defense has done vs projection / vs league average at this position
  H3 form      the player's own recent average miss
  H4 usage     recent snap share and opportunity share versus his season-to-date norm
  H5 shrink    the projection itself (are high projections too high?)
  H6 env       home, dome, wind, cold, short/long rest, Thursday, division game
  H7 qb        quarterback change on the team (pass catchers), new starter (QBs)
  H8 early     weeks 1-3
(H9, variance, reuses these features against the size of the miss; see evaluate.py.)
"""
from collections import defaultdict

import numpy as np

from . import common as C

GROUPS = {
    "H1 vegas": ["implied", "spread", "total"],
    "H2 opponent": ["opp_resid", "opp_fpa"],
    "H3 form": ["form4"],
    "H4 usage": ["snap_trend", "opp_share_trend"],
    "H5 shrink": ["proj_c"],
    "H6 env": ["home", "dome", "wind", "cold", "short_rest", "long_rest", "thursday", "div_game"],
    "H7 qb": ["qb_change"],
    "H8 early": ["early"],
}
ALL_FEATURES = [f for g in GROUPS.values() for f in g]

# who counts: "relevant" rows train and test the models; "startable" is the subset Jeff would ever start
RELEVANT = {"QB": 10.0, "RB": 5.0, "WR": 5.0, "TE": 4.0}
STARTABLE = {"QB": 14.0, "RB": 8.0, "WR": 8.0, "TE": 6.0}


def _t(r):
    return int(r["season"]) * 100 + int(r["week"])


def load_rows():
    """Dataset rows that were played and are fantasy-relevant, with numeric fields parsed, sorted by time."""
    rows = []
    for r in C.read_csv(C.DATASET):
        if r["gp"] != "1" or r["actual"] == "":
            continue
        r["season"], r["week"] = int(r["season"]), int(r["week"])
        r["t"] = _t(r)
        r["proj"], r["actual"] = float(r["proj"]), float(r["actual"])
        r["miss"] = r["actual"] - r["proj"]
        for k in list(r.keys()):
            if k.startswith("a_") or k in ("spread", "total", "implied", "opp_implied", "temp", "wind", "rest",
                                           "opp_rest", "home"):
                r[k] = C.fnum(r[k])
        rows.append(r)
    rows.sort(key=lambda r: (r["t"], r["pid"]))
    return rows


def build(rows):
    """Attach a feature dict r['x'] to every row. Missing values are None (mean-imputed later)."""
    # ---- team-week aggregates (actuals, used only for LATER weeks) ----
    team_opps = defaultdict(float)     # targets + rush attempts
    for r in rows:
        team_opps[(r["t"], r["team"])] += (r["a_rec_tgt"] or 0.0) + (r["a_rush_att"] or 0.0)

    # ---- H2: defense vs projection, and raw fantasy points allowed ----
    def_games = defaultdict(lambda: defaultdict(float))       # (season, defense, pos) -> {t: sum miss}
    for r in rows:
        if r["proj"] >= RELEVANT[r["pos"]]:
            def_games[(r["season"], r["opp"], r["pos"])][r["t"]] += r["miss"]
    fpa = defaultdict(dict)                                    # (season, defense, pos) -> {t: fpa}
    league_fpa = defaultdict(list)                             # (season, pos) -> [(t, fpa)]
    for d in C.read_csv(C.DEFENSE):
        t = int(d["season"]) * 100 + int(d["week"])
        for pos in C.POSITIONS:
            v = C.fnum(d["fpa_" + pos])
            if v is not None:
                fpa[(int(d["season"]), d["team"], pos)][t] = v
                league_fpa[(int(d["season"]), pos)].append((t, v))

    def prior_mean(series, t, min_n=2, shrink=3.0):
        vals = [v for tt, v in series.items() if tt < t]
        if len(vals) < min_n:
            return None, 0
        return float(np.mean(vals)) * len(vals) / (len(vals) + shrink), len(vals)

    # ---- H7: quarterback starts per team ----
    team_qbs = defaultdict(dict)                               # (season, team) -> {t: qb name}
    for r in rows:
        if r.get("team_qb"):
            team_qbs[(r["season"], r["team"])][r["t"]] = r["team_qb"]

    # ---- per-player history ----
    hist = defaultdict(list)                                   # (season, pid) -> prior rows this season
    pos_mean = {}
    for pos in C.POSITIONS:
        pos_mean[pos] = float(np.mean([r["proj"] for r in rows if r["pos"] == pos and r["proj"] >= RELEVANT[pos]]))

    for r in rows:
        x = {}
        pos, t = r["pos"], r["t"]
        # H1
        x["implied"], x["spread"], x["total"] = r["implied"], r["spread"], r["total"]
        # H2
        x["opp_resid"], _ = prior_mean(def_games[(r["season"], r["opp"], pos)], t)
        raw, n = prior_mean(fpa[(r["season"], r["opp"], pos)], t, shrink=0.0)
        if raw is None:
            x["opp_fpa"] = None
        else:
            lg = [v for tt, v in league_fpa[(r["season"], pos)] if tt < t]
            x["opp_fpa"] = (raw - float(np.mean(lg))) * n / (n + 3.0)
        # H3 / H4
        h = hist[(r["season"], r["pid"])]
        x["form4"] = float(np.mean([p["miss"] for p in h[-4:]])) if len(h) >= 2 else None

        def share(p, num, den):
            return (num / den) if (num is not None and den) else None
        snaps = [share(p, p["a_off_snp"], p["a_tm_off_snp"]) for p in h]
        snaps = [s for s in snaps if s is not None]
        x["snap_trend"] = (float(np.mean(snaps[-2:])) - float(np.mean(snaps))) if len(snaps) >= 4 else None
        opps = [share(p, (p["a_rec_tgt"] or 0.0) + (p["a_rush_att"] or 0.0), team_opps.get((p["t"], p["team"])))
                for p in h]
        opps = [s for s in opps if s is not None]
        x["opp_share_trend"] = (float(np.mean(opps[-2:])) - float(np.mean(opps))) if (len(opps) >= 4 and pos != "QB") else (0.0 if pos == "QB" else None)
        # H5
        x["proj_c"] = r["proj"] - pos_mean[pos]
        # H6
        dome = 1.0 if r["roof"] in ("dome", "closed") else 0.0
        x["home"], x["dome"] = r["home"], dome
        x["wind"] = 0.0 if dome else r["wind"]
        x["cold"] = 0.0 if dome else (None if r["temp"] is None else (1.0 if r["temp"] < 35 else 0.0))
        x["short_rest"] = None if r["rest"] is None else (1.0 if r["rest"] <= 5 else 0.0)
        x["long_rest"] = None if r["rest"] is None else (1.0 if r["rest"] >= 10 else 0.0)
        x["thursday"] = 1.0 if r["weekday"] == "Thursday" else 0.0
        x["div_game"] = C.fnum(r["div_game"], 0.0)
        # H7
        prior_q = [q for tt, q in sorted(team_qbs[(r["season"], r["team"])].items()) if tt < t][-4:]
        if not prior_q or not r.get("team_qb"):
            x["qb_change"] = 0.0
        elif pos == "QB":
            x["qb_change"] = 1.0 if (r["name"] == r["team_qb"] and r["name"] not in prior_q) else 0.0
        else:
            x["qb_change"] = 1.0 if r["team_qb"] != max(set(prior_q), key=prior_q.count) else 0.0
        # H8
        x["early"] = 1.0 if r["week"] <= 3 else 0.0
        r["x"] = x
        h.append(r)
    return rows


def matrix(rows, feats):
    """Rows x features array with NaN for missing."""
    X = np.full((len(rows), len(feats)), np.nan)
    for i, r in enumerate(rows):
        for j, f in enumerate(feats):
            v = r["x"].get(f)
            if v is not None:
                X[i, j] = v
    return X
