"""Can pre-game information beat the raw RotoWire projection out of sample?  python3 research/evaluate.py

Walk-forward ridge regression of the miss (actual - projection) on pre-game features, per position.
  tune   : ridge penalty chosen on 2024 (training data from 2022 on), full model only
  test   : 2025 + completed 2026 weeks; to predict week t the model sees only weeks before t
  verdict: paired bootstrap over whole weeks, Holm-corrected across the nine mean hypotheses
Writes research/data/results.json and research/data/model.json (final fit on all data, for cases/weekly).
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import common as C  # noqa: E402
from research import features as F  # noqa: E402
from research.baseline import pair_accuracy  # noqa: E402

LAMBDAS = [30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0, 30000.0, 100000.0]
TUNE_SEASON, TEST_FROM = 2024, 202501
N_BOOT = 5000
HYPS = ["H0 bias"] + list(F.GROUPS)
VAR_BASE = ["proj_c"]
VAR_EXTRA = ["proj_c", "implied", "spread_abs", "total", "dome", "wind", "early", "qb_change"]


def fit_ridge(X, y, lam):
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd[sd < 1e-9] = 1.0
    b0 = y.mean()
    if X.shape[1] == 0:                       # bias-only model
        return {"mu": mu, "sd": sd, "b0": b0, "beta": np.zeros(0)}
    Z = np.nan_to_num((X - mu) / sd)
    with np.errstate(all="ignore"):           # numpy 2.0 + macOS Accelerate raises spurious matmul warnings
        beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - b0))
    assert np.isfinite(beta).all()
    return {"mu": mu, "sd": sd, "b0": b0, "beta": beta}


def predict(m, X):
    if X.shape[1] == 0:
        return np.full(len(X), m["b0"])
    with np.errstate(all="ignore"):
        out = m["b0"] + np.nan_to_num((X - m["mu"]) / m["sd"]) @ m["beta"]
    assert np.isfinite(out).all()
    return out


def walk_forward(t, X, y, lam, test_mask):
    """Out-of-sample predictions for rows in test_mask; each test week is fit on strictly earlier weeks."""
    pred = np.full(len(y), np.nan)
    for tw in sorted(set(t[test_mask])):
        tr, te = t < tw, t == tw
        if tr.sum() < 200:
            continue
        pred[te] = predict(fit_ridge(X[tr], y[tr], lam), X[te])
    return pred


def week_sums(t, err_base, err_model):
    """Per-week sums so the bootstrap can resample whole weeks."""
    weeks = sorted(set(t))
    out = np.zeros((len(weeks), 5))
    for i, w in enumerate(weeks):
        k = t == w
        out[i] = [k.sum(), np.abs(err_base[k]).sum(), np.abs(err_model[k]).sum(),
                  (err_base[k] ** 2).sum(), (err_model[k] ** 2).sum()]
    return out


def boot(ws, rng):
    """MAE and RMSE improvement (baseline - model, positive = model better) with one-sided p-values."""
    def stats(s):
        n = s[:, 0].sum()
        return (s[:, 1].sum() - s[:, 2].sum()) / n, np.sqrt(s[:, 3].sum() / n) - np.sqrt(s[:, 4].sum() / n)
    d_mae, d_rmse = stats(ws)
    idx = rng.integers(0, len(ws), size=(N_BOOT, len(ws)))
    bs = np.array([stats(ws[i]) for i in idx])
    n = ws[:, 0].sum()
    return {"base_mae": float(ws[:, 1].sum() / n), "base_rmse": float(np.sqrt(ws[:, 3].sum() / n)),
            "d_mae": float(d_mae), "d_rmse": float(d_rmse),
            "mae_ci": [float(np.percentile(bs[:, 0], 2.5)), float(np.percentile(bs[:, 0], 97.5))],
            "rmse_ci": [float(np.percentile(bs[:, 1], 2.5)), float(np.percentile(bs[:, 1], 97.5))],
            "p_mae": float((bs[:, 0] <= 0).mean()), "p_rmse": float((bs[:, 1] <= 0).mean())}


def holm(pvals):
    order = sorted(pvals, key=pvals.get)
    out, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, pvals[k] * (len(order) - i)))
        out[k] = running
    return out


def main():
    rng = np.random.default_rng(20260918)
    rows = [r for r in F.build(F.load_rows()) if r["proj"] >= F.RELEVANT[r["pos"]]]
    for r in rows:
        r["x"]["spread_abs"] = None if r["x"]["spread"] is None else abs(r["x"]["spread"])
    res = {"lambda": {}, "full": {}, "groups": {}, "shuffle": {}, "variance": {}, "pairs": {}, "coef": {},
           "n_test_weeks": 0}
    model_out = {}
    pooled = defaultdict(list)      # name -> list of (t, err_base, err_model) per position
    test_rows = []

    for pos in C.POSITIONS:
        pr = [r for r in rows if r["pos"] == pos]
        t = np.array([r["t"] for r in pr])
        y = np.array([r["miss"] for r in pr])
        X = F.matrix(pr, F.ALL_FEATURES)
        tune = (t // 100) == TUNE_SEASON
        test = t >= TEST_FROM

        # ---- choose the ridge penalty on 2024 only
        best = None
        for lam in LAMBDAS:
            p = walk_forward(t, X, y, lam, tune)
            k = tune & ~np.isnan(p)
            rmse = float(np.sqrt(((y[k] - p[k]) ** 2).mean()))
            if best is None or rmse < best[1]:
                best = (lam, rmse)
        lam = best[0]
        res["lambda"][pos] = lam

        # ---- full model and each hypothesis group alone, on the test seasons
        # H0 is a plain bias correction (intercept only). Every other hypothesis is measured AGAINST the
        # bias-only model, otherwise each one gets credit for the intercept it carries.
        p_bias = walk_forward(t, X[:, []], y, lam, test)
        specs = [("FULL", F.ALL_FEATURES), ("H0 bias", [])] + list(F.GROUPS.items())
        for name, feats in specs:
            cols = [F.ALL_FEATURES.index(f) for f in feats]
            p = walk_forward(t, X[:, cols], y, lam, test)
            k = test & ~np.isnan(p)
            ref = np.zeros(len(y)) if name in ("FULL", "H0 bias") else p_bias
            pooled[name].append((t[k], y[k] - ref[k], y[k] - p[k]))
            ws = week_sums(t[k], y[k] - ref[k], y[k] - p[k])
            (res["full"] if name == "FULL" else res["groups"].setdefault(name, {}))[pos] = boot(ws, rng)
            if name == "FULL":
                pooled["FULL-vs-bias"].append((t[k], y[k] - p_bias[k], y[k] - p[k]))
            if name == "FULL":
                for r, pi, ki in zip(pr, p, k):
                    if ki:
                        r["adj"] = r["proj"] + float(pi)
                        test_rows.append(r)

        # ---- shuffle test: break the link between features and outcomes, improvement must vanish
        Xs = X[rng.permutation(len(X))]
        p = walk_forward(t, Xs, y, lam, test)
        k = test & ~np.isnan(p)
        pooled["SHUFFLE"].append((t[k], y[k], y[k] - p[k]))

        # ---- H9: is the SIZE of the miss predictable beyond "bigger projection, bigger miss"?
        ya = np.abs(y)
        Xb, Xe = F.matrix(pr, VAR_BASE), F.matrix(pr, VAR_EXTRA)
        lam_v = 100.0                          # light penalty: the projection-size effect is large and stable
        pb, pe = walk_forward(t, Xb, ya, lam_v, test), walk_forward(t, Xe, ya, lam_v, test)
        k = test & ~np.isnan(pb) & ~np.isnan(pe)
        const = np.array([ya[t < tw].mean() for tw in t[k]])
        pooled["H9 base-vs-const"].append((t[k], ya[k] - const, ya[k] - pb[k]))
        pooled["H9 extra-vs-base"].append((t[k], ya[k] - pb[k], ya[k] - pe[k]))
        mb = fit_ridge(Xb, ya, lam_v)
        res["variance"][pos] = {"abs_miss_at_mean_proj": float(mb["b0"]),
                                "abs_miss_per_proj_point": float(mb["beta"][0] / mb["sd"][0])}

        # ---- final fit on everything: coefficients in fantasy points per one standard deviation of the feature
        m = fit_ridge(X, y, lam)
        res["coef"][pos] = {f: {"per_sd": float(b), "sd": float(s)} for f, b, s in zip(F.ALL_FEATURES, m["beta"], m["sd"])}
        res["coef"][pos]["_intercept"] = float(m["b0"])
        model_out[pos] = {"lambda": lam, "features": F.ALL_FEATURES, "mu": m["mu"].tolist(), "sd": m["sd"].tolist(),
                          "b0": float(m["b0"]), "beta": m["beta"].tolist()}

    # ---- pooled across positions
    def pooled_boot(name):
        t = np.concatenate([a for a, _, _ in pooled[name]])
        eb = np.concatenate([b for _, b, _ in pooled[name]])
        em = np.concatenate([c for _, _, c in pooled[name]])
        return boot(week_sums(t, eb, em), rng), len(set(t)), len(t)
    res["full"]["ALL"], res["n_test_weeks"], res["n_test_rows"] = pooled_boot("FULL")
    res["full_vs_bias"] = pooled_boot("FULL-vs-bias")[0]
    for name in HYPS:
        res["groups"][name]["ALL"] = pooled_boot(name)[0]
    res["shuffle"] = pooled_boot("SHUFFLE")[0]
    res["variance"]["base_vs_const"] = pooled_boot("H9 base-vs-const")[0]
    res["variance"]["extra_vs_base"] = pooled_boot("H9 extra-vs-base")[0]
    res["holm_rmse"] = holm({g: res["groups"][g]["ALL"]["p_rmse"] for g in HYPS})

    # ---- the decision metric: close start/sit calls on the test weeks
    for key in ("proj", "adj"):
        acc = pair_accuracy(test_rows, key, 4.0)
        res["pairs"][key] = {p: {"acc": acc[p][0] / float(acc[p][1]), "n": acc[p][1]} for p in C.POSITIONS}
        res["pairs"][key]["ALL"] = {"acc": sum(acc[p][0] for p in C.POSITIONS) / float(sum(acc[p][1] for p in C.POSITIONS)),
                                    "n": sum(acc[p][1] for p in C.POSITIONS)}
    # pairs where the two rankings disagree: who is right?
    dis = [0, 0]
    groups = defaultdict(list)
    for r in test_rows:
        if r["proj"] >= F.STARTABLE[r["pos"]]:
            groups[(r["t"], r["pos"])].append(r)
    for g in groups.values():
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g[i], g[j]
                if abs(a["proj"] - b["proj"]) > 4.0 or a["actual"] == b["actual"]:
                    continue
                if (a["proj"] - b["proj"]) * (a["adj"] - b["adj"]) < 0:
                    dis[1] += 1
                    dis[0] += 1 if (a["adj"] - b["adj"]) * (a["actual"] - b["actual"]) > 0 else 0
    res["pairs"]["disagree"] = {"n": dis[1], "adj_right": dis[0] / float(dis[1]) if dis[1] else None}

    with open(os.path.join(C.DATA_DIR, "results.json"), "w") as f:
        json.dump(res, f, indent=1)
    with open(os.path.join(C.DATA_DIR, "model.json"), "w") as f:
        json.dump(model_out, f)

    # ---- print
    def line(name, d, extra=""):
        print("  %-16s RMSE gain %+.3f  [%+.3f, %+.3f]  p=%.3f    MAE gain %+.3f  p=%.3f %s"
              % (name, d["d_rmse"], d["rmse_ci"][0], d["rmse_ci"][1], d["p_rmse"], d["d_mae"], d["p_mae"], extra))
    print("Test set: %d player-games over %d weeks (2025 + completed 2026). Gains are in fantasy points per "
          "player-game;\npositive = better than raw RotoWire. Ridge penalty chosen on 2024: %s\n"
          % (res["n_test_rows"], res["n_test_weeks"], res["lambda"]))
    print("FULL MODEL (all features)")
    for k in list(C.POSITIONS) + ["ALL"]:
        line(k, res["full"][k])
    line("ALL vs bias-only", res["full_vs_bias"])
    print("\nEACH HYPOTHESIS ALONE, all positions pooled. H0 is measured against raw RotoWire; H1-H8 against the\n"
          "bias-only model, so they get no credit for the intercept. Holm = corrected for testing nine ideas.")
    for g in HYPS:
        line(g, res["groups"][g]["ALL"], "  Holm p=%.3f" % res["holm_rmse"][g])
    print("\nBY POSITION, RMSE gain of each hypothesis alone")
    print("  %-16s" % "" + "".join("%18s" % p for p in C.POSITIONS))
    for g in HYPS:
        print("  %-16s" % g + "".join("   %+.3f (p=%.2f)" % (res["groups"][g][p]["d_rmse"], res["groups"][g][p]["p_rmse"]) for p in C.POSITIONS))
    print("\nSHUFFLE CONTROL (features scrambled, should show no gain)")
    line("shuffled", res["shuffle"])
    print("\nH9 SIZE OF THE MISS (predicting |miss|)")
    line("proj vs constant", res["variance"]["base_vs_const"])
    line("+vegas/env", res["variance"]["extra_vs_base"])
    for p in C.POSITIONS:
        v = res["variance"][p]
        print("    %s: typical |miss| %.2f at an average projection, %+.3f per extra projected point" % (p, v["abs_miss_at_mean_proj"], v["abs_miss_per_proj_point"]))
    print("\nSTART/SIT, close calls (projections within 4), test weeks")
    for k in list(C.POSITIONS) + ["ALL"]:
        print("  %-4s RotoWire %.1f%%   adjusted %.1f%%   (n=%d pairs)" % (k, 100 * res["pairs"]["proj"][k]["acc"], 100 * res["pairs"]["adj"][k]["acc"], res["pairs"]["proj"][k]["n"]))
    d = res["pairs"]["disagree"]
    print("  pairs where the adjusted ranking flips RotoWire's: %d, adjusted is right %.1f%% of the time" % (d["n"], 100 * (d["adj_right"] or 0)))


if __name__ == "__main__":
    main()
