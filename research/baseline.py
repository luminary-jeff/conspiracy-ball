"""How wrong is RotoWire, and where?  python3 research/baseline.py

Prints (and saves to research/data/baseline.json): error by position, calibration by projection bucket,
the share of games that look like mid-game injury exits, and start/sit pair accuracy.
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402

BUCKETS = {"QB": [10, 14, 17, 20, 99], "RB": [5, 8, 11, 14, 17, 99], "WR": [5, 8, 11, 14, 99], "TE": [4, 6, 8, 11, 99]}


def pair_accuracy(rows, score_key="proj", within=4.0):
    """Among same-week, same-position startable pairs whose RotoWire projections are within `within`
    points, how often does the player ranked higher by `score_key` score more?"""
    groups = defaultdict(list)
    for r in rows:
        if r["proj"] >= F.STARTABLE[r["pos"]]:
            groups[(r["t"], r["pos"])].append(r)
    out = defaultdict(lambda: [0, 0])
    for (t, pos), g in groups.items():
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g[i], g[j]
                if abs(a["proj"] - b["proj"]) > within or a[score_key] == b[score_key] or a["actual"] == b["actual"]:
                    continue
                hi, lo = (a, b) if a[score_key] > b[score_key] else (b, a)
                out[pos][0] += 1 if hi["actual"] > lo["actual"] else 0
                out[pos][1] += 1
    return out


def injury_exit(r, hist):
    """Snap share under half of his prior norm: probably left the game hurt (or was benched)."""
    prior = [p["a_off_snp"] / p["a_tm_off_snp"] for p in hist if p["a_off_snp"] and p["a_tm_off_snp"]]
    if len(prior) < 3 or not r["a_tm_off_snp"] or r["a_off_snp"] is None:
        return False
    return (r["a_off_snp"] / r["a_tm_off_snp"]) < 0.5 * float(np.mean(prior[-4:]))


def main():
    rows = [r for r in F.load_rows() if r["proj"] >= F.RELEVANT[r["pos"]]]
    hist = defaultdict(list)
    for r in rows:
        r["exit"] = injury_exit(r, hist[(r["season"], r["pid"])])
        hist[(r["season"], r["pid"])].append(r)
    res = {"positions": {}, "buckets": {}, "pairs": {}}

    print("RotoWire weekly projections vs actuals, Conspiracy Ball scoring, 2022-2026 (fantasy-relevant players)\n")
    print("  pos      n   mean proj  mean actual    bias     MAE    RMSE   sd(miss)  corr   R2    injury-exit share")
    for pos in C.POSITIONS:
        pr = [r for r in rows if r["pos"] == pos]
        p, a = np.array([r["proj"] for r in pr]), np.array([r["actual"] for r in pr])
        m = a - p
        corr = float(np.corrcoef(p, a)[0, 1])
        ex = float(np.mean([r["exit"] for r in pr]))
        d = {"n": len(pr), "mean_proj": float(p.mean()), "mean_actual": float(a.mean()), "bias": float(m.mean()),
             "mae": float(np.abs(m).mean()), "rmse": float(np.sqrt((m ** 2).mean())), "sd": float(m.std()),
             "corr": corr, "r2": corr ** 2, "exit_share": ex}
        clean = np.array([r["miss"] for r in pr if not r["exit"]])
        d["mae_no_exits"], d["bias_no_exits"] = float(np.abs(clean).mean()), float(clean.mean())
        res["positions"][pos] = d
        print("  %-3s %6d   %8.2f   %10.2f  %+6.2f  %6.2f  %6.2f   %7.2f  %5.2f  %4.2f   %5.1f%%"
              % (pos, d["n"], d["mean_proj"], d["mean_actual"], d["bias"], d["mae"], d["rmse"], d["sd"], corr,
                 corr ** 2, 100 * ex))
    print("\n  excluding probable injury exits:  " + "  ".join(
        "%s bias %+.2f MAE %.2f" % (p, res["positions"][p]["bias_no_exits"], res["positions"][p]["mae_no_exits"])
        for p in C.POSITIONS))

    print("\nCalibration: when RotoWire says X, what happens on average?  (se = standard error of the mean)")
    for pos in C.POSITIONS:
        print("  %s" % pos)
        lo = F.RELEVANT[pos]
        res["buckets"][pos] = []
        for hi in BUCKETS[pos][1:]:
            b = [r for r in rows if r["pos"] == pos and lo <= r["proj"] < hi]
            if len(b) >= 20:
                m = np.array([r["miss"] for r in b])
                a = np.array([r["actual"] for r in b])
                se = float(m.std() / np.sqrt(len(b)))
                row = {"lo": lo, "hi": hi, "n": len(b), "mean_proj": float(np.mean([r["proj"] for r in b])),
                       "mean_actual": float(a.mean()), "bias": float(m.mean()), "se": se, "sd": float(m.std()),
                       "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90))}
                res["buckets"][pos].append(row)
                print("    proj %4.0f-%-4s n=%5d  mean proj %5.1f  mean actual %5.1f  bias %+5.2f (se %.2f)  sd %5.2f  10th-90th pct actual %4.1f to %4.1f"
                      % (lo, ("%.0f" % hi) if hi < 99 else "up", len(b), row["mean_proj"], row["mean_actual"],
                         row["bias"], se, row["sd"], row["p10"], row["p90"]))
            lo = hi

    print("\nStart/sit accuracy: startable pairs, same week and position, how often the higher projection scores more")
    for within in (2.0, 4.0, 99.0):
        acc = pair_accuracy(rows, "proj", within)
        label = "within %.0f pts" % within if within < 99 else "any gap     "
        res["pairs"][str(within)] = {p: {"acc": acc[p][0] / float(acc[p][1]), "n": acc[p][1]} for p in C.POSITIONS}
        print("  %s  " % label + "   ".join("%s %.1f%% (n=%d)" % (p, 100.0 * acc[p][0] / acc[p][1], acc[p][1]) for p in C.POSITIONS))

    with open(os.path.join(C.DATA_DIR, "baseline.json"), "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
