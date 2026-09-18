# Can pre-game information beat RotoWire? Projection accuracy study

Generated 2026-09-18 by `research/report.py`. Research only; the tool's recommendations do not use any of this.

## Verdict

**Answer 1, with a footnote. The improvement is statistically real and too small to matter.**

- Tested on 3395 player-games across 19 weeks the model never saw while fitting (2025 plus completed 2026 weeks).
- All pre-game features together cut the typical error (RMSE) from 6.626 to 6.602 fantasy points per player-game. That is 0.36% better, p<0.001.
- On the decision that matters, close start/sit calls, RotoWire's higher projection wins 57.0% of the time and the adjusted projection 57.1%.
- When the adjusted ranking disagrees with RotoWire, it is right 51.1% of the time over 1037 pairs. That is a coin.
- Ideas that survive the multiple-testing correction: H1 Vegas line (implied total, spread, total).
- The scrambled-feature control shows a gain of +0.001 (p=0.38), so the pipeline is not manufacturing signal.

RotoWire already prices in nearly everything knowable before kickoff. What is left is noise: a weekly
projection explains only a small share of the variance in what a fantasy-relevant player actually scores:
QB 12%, RB 22%, WR 17%, TE 11%.

## Goff and Purdy

| game | RotoWire | adjusted | actual | miss | how rare |
|---|---|---|---|---|---|
| Jared Goff wk 1 vs NO | 19.0 | 19.2 | 16.4 | -2.5 (-0.3 sd) | 44% of 763 similar QBs did this or more extreme |
| Brock Purdy wk 1 vs LAR | 18.5 | 17.9 | 22.1 | +3.6 (0.5 sd) | 28% of 852 similar QBs did this or more extreme |
| Jared Goff wk 2 vs BUF | 16.3 | 16.5 | 29.8 | +13.5 (1.9 sd) | 4% of 1064 similar QBs did this or more extreme |
| Brock Purdy wk 2 vs MIA | 20.0 | 19.7 | pending | | |

The adjusted model would have made the same start in both weeks. Everything it knows moved Goff's
week 2 number by a fraction of a point against a miss of more than thirteen.

How often the "wrong" quarterback wins, from every pair of startable quarterbacks since 2022:

| projection gap | lower-projected QB scores more | and by 10 or more | pairs |
|---|---|---|---|
| 0.0 to 1.0 pts | 47% | 14% | 4888 |
| 1.0 to 2.5 pts | 44% | 13% | 6093 |
| 2.5 to 4.5 pts | 40% | 11% | 5383 |

Week 1 was a half-point gap, so a 47% event. Week 2 was a 3.7-point gap, a 40% event. Losing both is
roughly a one-in-five outcome. It is ordinary bad luck, not a flaw in the choice.

## How wrong RotoWire is

| pos | games | bias | mean abs error | RMSE | variance explained |
|---|---|---|---|---|---|
| QB | 2186 | -0.36 | 5.87 | 7.39 | 12% |
| RB | 3350 | +0.09 | 5.34 | 6.86 | 22% |
| WR | 5217 | -0.27 | 5.08 | 6.46 | 17% |
| TE | 2128 | +0.06 | 4.21 | 5.52 | 11% |

Calibration by projection size. Bias is actual minus projected; the last column is the 10th to 90th percentile of what actually happened.

| pos | projected | games | bias (se) | sd of miss | actual range |
|---|---|---|---|---|---|
| QB | 10 to 14 | 415 | +0.10 (0.34) | 6.9 | 4.0 to 21.8 |
| QB | 14 to 17 | 764 | -0.12 (0.26) | 7.2 | 6.5 to 25.1 |
| QB | 17 to 20 | 644 | -0.55 (0.30) | 7.6 | 8.0 to 27.6 |
| QB | 20 and up | 363 | -1.09 (0.42) | 8.0 | 10.6 to 31.1 |
| RB | 5 to 8 | 927 | -0.12 (0.18) | 5.3 | 0.7 to 13.9 |
| RB | 8 to 11 | 896 | +0.24 (0.22) | 6.6 | 2.5 to 19.4 |
| RB | 11 to 14 | 886 | +0.43 (0.25) | 7.3 | 4.8 to 22.7 |
| RB | 14 to 17 | 444 | -0.01 (0.38) | 8.0 | 6.0 to 25.9 |
| RB | 17 and up | 197 | -0.91 (0.64) | 8.9 | 7.3 to 30.2 |
| WR | 5 to 8 | 2112 | -0.26 (0.12) | 5.5 | 0.2 to 14.3 |
| WR | 8 to 11 | 1725 | -0.26 (0.15) | 6.4 | 2.0 to 18.2 |
| WR | 11 to 14 | 990 | -0.34 (0.24) | 7.4 | 3.3 to 21.6 |
| WR | 14 and up | 390 | -0.17 (0.43) | 8.5 | 5.2 to 26.8 |
| TE | 4 to 6 | 802 | +0.28 (0.17) | 4.9 | 0.0 to 12.2 |
| TE | 6 to 8 | 727 | +0.02 (0.20) | 5.5 | 1.2 to 14.1 |
| TE | 8 to 11 | 478 | +0.11 (0.28) | 6.2 | 2.6 to 18.1 |
| TE | 11 and up | 121 | -1.28 (0.61) | 6.7 | 3.5 to 21.0 |

Two things stand out. The very top projections run hot: quarterbacks projected 20 or more and tight ends
projected 11 or more come in about a point under. And the spread of outcomes widens as the projection
grows, which matters for win probability more than for start/sit.

## Each idea, tested alone

RMSE gain in fantasy points per player-game on the unseen weeks, all positions pooled. H0 is measured
against raw RotoWire; the rest against the bias-only model so they get no credit for the intercept.

| idea | gain | 95% interval | p | corrected p |
|---|---|---|---|---|
| H0 flat bias correction per position | +0.007 | -0.000 to +0.014 | 0.028 | 0.221 |
| H1 Vegas line (implied total, spread, total) | +0.006 | +0.002 to +0.010 | 0.001 | 0.009 |
| H2 opposing defense's record | +0.003 | -0.002 to +0.009 | 0.091 | 0.636 |
| H3 player's recent misses | -0.000 | -0.001 to +0.001 | 0.667 | 1.000 |
| H4 snap and opportunity trend | +0.001 | -0.001 to +0.003 | 0.299 | 1.000 |
| H5 shrink big projections | +0.001 | -0.002 to +0.004 | 0.315 | 1.000 |
| H6 venue, weather, rest, Thursday | +0.006 | -0.003 to +0.015 | 0.091 | 0.636 |
| H7 quarterback change | -0.000 | -0.001 to +0.000 | 0.687 | 1.000 |
| H8 weeks 1-3 | -0.000 | -0.004 to +0.004 | 0.554 | 1.000 |

Full model by position:

| pos | baseline RMSE | gain | p |
|---|---|---|---|
| QB | 7.668 | +0.070 | 0.000 |
| RB | 7.045 | -0.004 | 0.670 |
| WR | 6.265 | +0.024 | 0.000 |
| TE | 5.580 | +0.021 | 0.247 |
| ALL | 6.626 | +0.024 | 0.000 |

H9, the size of the miss: projection size predicts it (gain +0.087, p=0.000). Adding the betting line,
venue and weather on top adds -0.005 (p=0.892), which is nothing.

## H10, added after the first report: player prop lines

DraftKings lines served by ESPN, collected forward from 2026 week 1 (2025 was already purged, so there is
no back-test). The feed has yardage and reception lines but no prices, so touchdowns stay RotoWire's.
No fitting: the market's numbers simply replace RotoWire's yardage and catch components.

| week | players with a line | MAE RotoWire | MAE prop-swapped |
|---|---|---|---|
| 1 | 183 | 5.765 | 5.542 |
| all | 183 | 5.765 | 5.542 |

Props are ahead so far. Do not act on this before roughly 1,500 player-games (about week 8). Weeks captured only after the
games use opening lines, because ESPN overwrites the closing line with the last in-game live line. From
week 2 on, the Thursday and Sunday runs snapshot before kickoff.

## Method and caveats

- Data: Sleeper's archived RotoWire weekly projections and Sportradar actuals 2022 to 2026, scored with the
  league's settings; closing lines, weather and rest from nflverse `games.csv`. Players projected under
  QB 10, RB/WR 5, TE 4 are excluded.
- Leak check: Sleeper freezes a projection at kickoff. Gibbs and Goff showed their exact pre-game values the
  day after their week 2 game. `weekly.py` keeps re-verifying this against saved snapshots.
- Model: ridge regression of the miss on the features, per position. To predict a week it sees only earlier
  weeks. Penalty chosen on 2024 (QB 3000, RB 10000, WR 100000, TE 1000); 2025 and 2026 were never used for tuning.
- Deviation from the plan: the plan held out 2026 as the final exam. One completed week is too few games to
  test anything, so 2025 plus 2026 is the test set and 2024 is the tuning set.
- Significance: bootstrap resampling whole weeks, Holm correction across nine ideas.
- The announced starting quarterback comes from the box score, so a true game-time surprise would leak in.
  It did not help anyway.
- FantasyPros could not be back-tested; past weekly rankings are not published. `weekly.py` now snapshots
  them each week, so a two-source blend can be tested from mid-season on.
- Not tested: practice-report detail beyond the injury tag. Prop lines are being collected (H10 above).

## Reproduce

```
python3 research/collect.py     # data set (cached; ~150 calls cold)
python3 research/baseline.py    # how wrong RotoWire is
python3 research/evaluate.py    # hypothesis tests
python3 research/cases.py       # Goff and Purdy
python3 research/report.py      # this file
python3 research/apply.py       # adjusted numbers for the current week
python3 research/props.py       # --snapshot / --evaluate player prop lines (H10)
python3 research/hitrates.py    # close-call hit rates pasted into ff/season.py CLOSE_CALL_HIT
python3 research/weekly.py      # snapshot + refresh + scoreboards, run each week
```
