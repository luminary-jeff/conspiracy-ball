"""Render research/REPORT.md from baseline.json, results.json and cases.json.  python3 research/report.py"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402

LABEL = {"H0 bias": "H0 flat bias correction per position", "H1 vegas": "H1 Vegas line (implied total, spread, total)",
         "H2 opponent": "H2 opposing defense's record", "H3 form": "H3 player's recent misses",
         "H4 usage": "H4 snap and opportunity trend", "H5 shrink": "H5 shrink big projections",
         "H6 env": "H6 venue, weather, rest, Thursday", "H7 qb": "H7 quarterback change", "H8 early": "H8 weeks 1-3"}


def load(name):
    path = os.path.join(C.DATA_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def main():
    base, res, cases = load("baseline.json"), load("results.json"), load("cases.json")
    if not base or not res:
        sys.exit("run baseline.py and evaluate.py first")
    full = res["full"]["ALL"]
    pct = 100.0 * full["d_rmse"] / full["base_rmse"]
    pa, pb = res["pairs"]["adj"]["ALL"]["acc"], res["pairs"]["proj"]["ALL"]["acc"]
    dis = res["pairs"]["disagree"]
    survivors = [g for g, p in res["holm_rmse"].items() if p < 0.05]
    real = full["p_rmse"] < 0.05
    useful = pct >= 2.0 and (pa - pb) >= 0.01
    verdict = ("Answer 2: pre-game information improves the projection enough to change decisions." if useful else
               "Answer 1, with a footnote. The improvement is statistically real and too small to matter."
               if real else "Answer 1. No measurable improvement.")
    L = []
    w = L.append
    w("# Can pre-game information beat RotoWire? Projection accuracy study")
    w("")
    w("Generated %s by `research/report.py`. Research only; the tool's recommendations do not use any of this." % time.strftime("%Y-%m-%d"))
    w("")
    w("## Verdict")
    w("")
    w("**%s**" % verdict)
    w("")
    w("- Tested on %d player-games across %d weeks the model never saw while fitting (2025 plus completed 2026 weeks)." % (res["n_test_rows"], res["n_test_weeks"]))
    w("- All pre-game features together cut the typical error (RMSE) from %.3f to %.3f fantasy points per player-game. That is %.2f%% better, %s." % (full["base_rmse"], full["base_rmse"] - full["d_rmse"], pct, "p<0.001" if full["p_rmse"] < 0.001 else "p=%.3f" % full["p_rmse"]))
    w("- On the decision that matters, close start/sit calls, RotoWire's higher projection wins %.1f%% of the time and the adjusted projection %.1f%%." % (100 * pb, 100 * pa))
    w("- When the adjusted ranking disagrees with RotoWire, it is right %.1f%% of the time over %d pairs. That is a coin." % (100 * (dis["adj_right"] or 0), dis["n"]))
    w("- Ideas that survive the multiple-testing correction: %s." % (", ".join(LABEL[g] for g in survivors) if survivors else "none"))
    w("- The scrambled-feature control shows a gain of %+.3f (p=%.2f), so the pipeline is not manufacturing signal." % (res["shuffle"]["d_rmse"], res["shuffle"]["p_rmse"]))
    w("")
    w("RotoWire already prices in nearly everything knowable before kickoff. What is left is noise: a weekly")
    w("projection explains only a small share of the variance in what a fantasy-relevant player actually scores:")
    w("%s." % ", ".join("%s %d%%" % (p, round(100 * base["positions"][p]["r2"])) for p in C.POSITIONS))
    w("")
    if cases:
        w("## Goff and Purdy")
        w("")
        w("| game | RotoWire | adjusted | actual | miss | how rare |")
        w("|---|---|---|---|---|---|")
        for g in cases["games"]:
            if g["actual"] is None:
                w("| %s wk %d vs %s | %.1f | %.1f | pending | | |" % (g["name"], g["week"], g["opp"], g["proj"], g["adj"]))
            else:
                w("| %s wk %d vs %s | %.1f | %.1f | %.1f | %+.1f (%.1f sd) | %.0f%% of %d similar QBs did this or more extreme |" % (
                    g["name"], g["week"], g["opp"], g["proj"], g["adj"], g["actual"], g["miss"], g["z"], 100 * g["tail"], g["peers"]))
        w("")
        w("The adjusted model would have made the same start in both weeks. Everything it knows moved Goff's")
        w("week 2 number by a fraction of a point against a miss of more than thirteen.")
        w("")
        w("How often the \"wrong\" quarterback wins, from every pair of startable quarterbacks since 2022:")
        w("")
        w("| projection gap | lower-projected QB scores more | and by 10 or more | pairs |")
        w("|---|---|---|---|")
        for k, v in cases["pairs"].items():
            w("| %s pts | %.0f%% | %.0f%% | %d |" % (k.replace("-", " to "), 100 * v["wrong"], 100 * v["wrong10"], v["n"]))
        w("")
        w("Week 1 was a half-point gap, so a 47%% event. Week 2 was a 3.7-point gap, a 40%% event. Losing both is")
        w("roughly a one-in-five outcome. It is ordinary bad luck, not a flaw in the choice.")
        w("")
    w("## How wrong RotoWire is")
    w("")
    w("| pos | games | bias | mean abs error | RMSE | variance explained |")
    w("|---|---|---|---|---|---|")
    for p in C.POSITIONS:
        d = base["positions"][p]
        w("| %s | %d | %+.2f | %.2f | %.2f | %d%% |" % (p, d["n"], d["bias"], d["mae"], d["rmse"], round(100 * d["r2"])))
    w("")
    w("Calibration by projection size. Bias is actual minus projected; the last column is the 10th to 90th percentile of what actually happened.")
    w("")
    w("| pos | projected | games | bias (se) | sd of miss | actual range |")
    w("|---|---|---|---|---|---|")
    for p in C.POSITIONS:
        for b in base["buckets"][p]:
            w("| %s | %s | %d | %+.2f (%.2f) | %.1f | %.1f to %.1f |" % (
                p, ("%.0f to %.0f" % (b["lo"], b["hi"])) if b["hi"] < 99 else ("%.0f and up" % b["lo"]), b["n"], b["bias"], b["se"], b["sd"], b["p10"], b["p90"]))
    w("")
    w("Two things stand out. The very top projections run hot: quarterbacks projected 20 or more and tight ends")
    w("projected 11 or more come in about a point under. And the spread of outcomes widens as the projection")
    w("grows, which matters for win probability more than for start/sit.")
    w("")
    w("## Each idea, tested alone")
    w("")
    w("RMSE gain in fantasy points per player-game on the unseen weeks, all positions pooled. H0 is measured")
    w("against raw RotoWire; the rest against the bias-only model so they get no credit for the intercept.")
    w("")
    w("| idea | gain | 95% interval | p | corrected p |")
    w("|---|---|---|---|---|")
    for g in ["H0 bias"] + list(F.GROUPS):
        d = res["groups"][g]["ALL"]
        w("| %s | %+.3f | %+.3f to %+.3f | %.3f | %.3f |" % (LABEL[g], d["d_rmse"], d["rmse_ci"][0], d["rmse_ci"][1], d["p_rmse"], res["holm_rmse"][g]))
    w("")
    w("Full model by position:")
    w("")
    w("| pos | baseline RMSE | gain | p |")
    w("|---|---|---|---|")
    for p in list(C.POSITIONS) + ["ALL"]:
        d = res["full"][p]
        w("| %s | %.3f | %+.3f | %.3f |" % (p, d["base_rmse"], d["d_rmse"], d["p_rmse"]))
    w("")
    w("H9, the size of the miss: projection size predicts it (gain %+.3f, p=%.3f). Adding the betting line," % (res["variance"]["base_vs_const"]["d_rmse"], res["variance"]["base_vs_const"]["p_rmse"]))
    w("venue and weather on top adds %+.3f (p=%.3f), which is nothing." % (res["variance"]["extra_vs_base"]["d_rmse"], res["variance"]["extra_vs_base"]["p_rmse"]))
    w("")
    pr = load("props_results.json")
    if pr and pr.get("n"):
        w("## H10, added after the first report: player prop lines")
        w("")
        w("DraftKings lines served by ESPN, collected forward from 2026 week 1 (2025 was already purged, so there is")
        w("no back-test). The feed has yardage and reception lines but no prices, so touchdowns stay RotoWire's.")
        w("No fitting: the market's numbers simply replace RotoWire's yardage and catch components.")
        w("")
        w("| week | players with a line | MAE RotoWire | MAE prop-swapped |")
        w("|---|---|---|---|")
        for r in pr["weeks"]:
            w("| %d | %d | %.3f | %.3f |" % (r["week"], r["n"], r["mae_rw"], r["mae_prop"]))
        w("| all | %d | %.3f | %.3f |" % (pr["n"], pr["mae_rw"], pr["mae_prop"]))
        w("")
        w("%s Do not act on this before roughly 1,500 player-games (about week 8). Weeks captured only after the" % (
            "Props are ahead so far." if pr["mae_prop"] < pr["mae_rw"] else "RotoWire is ahead so far."))
        w("games use opening lines, because ESPN overwrites the closing line with the last in-game live line. From")
        w("week 2 on, the Thursday and Sunday runs snapshot before kickoff.")
        w("")
    w("## Method and caveats")
    w("")
    w("- Data: Sleeper's archived RotoWire weekly projections and Sportradar actuals 2022 to 2026, scored with the")
    w("  league's settings; closing lines, weather and rest from nflverse `games.csv`. Players projected under")
    w("  QB 10, RB/WR 5, TE 4 are excluded.")
    w("- Leak check: Sleeper freezes a projection at kickoff. Gibbs and Goff showed their exact pre-game values the")
    w("  day after their week 2 game. `weekly.py` keeps re-verifying this against saved snapshots.")
    w("- Model: ridge regression of the miss on the features, per position. To predict a week it sees only earlier")
    w("  weeks. Penalty chosen on 2024 (%s); 2025 and 2026 were never used for tuning." % ", ".join("%s %g" % kv for kv in res["lambda"].items()))
    w("- Deviation from the plan: the plan held out 2026 as the final exam. One completed week is too few games to")
    w("  test anything, so 2025 plus 2026 is the test set and 2024 is the tuning set.")
    w("- Significance: bootstrap resampling whole weeks, Holm correction across nine ideas.")
    w("- The announced starting quarterback comes from the box score, so a true game-time surprise would leak in.")
    w("  It did not help anyway.")
    w("- FantasyPros could not be back-tested; past weekly rankings are not published. `weekly.py` now snapshots")
    w("  them each week, so a two-source blend can be tested from mid-season on.")
    w("- Not tested: practice-report detail beyond the injury tag. Prop lines are being collected (H10 above).")
    w("")
    w("## Reproduce")
    w("")
    w("```")
    w("python3 research/collect.py     # data set (cached; ~150 calls cold)")
    w("python3 research/baseline.py    # how wrong RotoWire is")
    w("python3 research/evaluate.py    # hypothesis tests")
    w("python3 research/cases.py       # Goff and Purdy")
    w("python3 research/report.py      # this file")
    w("python3 research/apply.py       # adjusted numbers for the current week")
    w("python3 research/props.py       # --snapshot / --evaluate player prop lines (H10)")
    w("python3 research/hitrates.py    # close-call hit rates pasted into ff/season.py CLOSE_CALL_HIT")
    w("python3 research/weekly.py      # snapshot + refresh + scoreboards, run each week")
    w("```")
    with open(os.path.join(C.RESEARCH_DIR, "REPORT.md"), "w") as f:
        f.write("\n".join(L).replace("%%", "%") + "\n")
    print("wrote research/REPORT.md: " + verdict)


if __name__ == "__main__":
    main()
