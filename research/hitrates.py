"""How often does the higher RotoWire projection win a close start/sit call?  python3 research/hitrates.py

Prints the table that ff/season.py carries as CLOSE_CALL_HIT (the tool never imports research/, so the
numbers are pasted there; re-run this after a season and update them if they move).
Pairs: same week, both startable, by position; FLEX pools RB/WR/TE across positions.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from research import features as F  # noqa: E402

GAPS = [(0.0, 1.0), (1.0, 2.5), (2.5, 4.5)]


def main():
    rows = [r for r in F.load_rows() if r["proj"] >= F.STARTABLE[r["pos"]]]
    weeks = defaultdict(list)
    for r in rows:
        weeks[r["t"]].append(r)
    table = {}
    for label, ok in (("QB", {"QB"}), ("RB", {"RB"}), ("WR", {"WR"}), ("TE", {"TE"}), ("FLEX", {"RB", "WR", "TE"})):
        table[label] = []
        for lo, hi in GAPS:
            n = win = 0
            for g in weeks.values():
                g = [r for r in g if r["pos"] in ok]
                for i in range(len(g)):
                    for j in range(i + 1, len(g)):
                        a, b = (g[i], g[j]) if g[i]["proj"] >= g[j]["proj"] else (g[j], g[i])
                        if label == "FLEX" and a["pos"] == b["pos"]:
                            continue
                        if lo <= a["proj"] - b["proj"] < hi and a["actual"] != b["actual"]:
                            n += 1
                            win += a["actual"] > b["actual"]
            table[label].append((hi, round(win / float(n), 2), n))
    print("CLOSE_CALL_HIT = {   # (gap below, P(higher projection scores more)); pairs behind each: see research/hitrates.py")
    for k, v in table.items():
        print('    "%s": [%s],   # n = %s' % (k, ", ".join("(%.1f, %.2f)" % (hi, p) for hi, p, _ in v), ", ".join(str(n) for _, _, n in v)))
    print("}")


if __name__ == "__main__":
    main()
