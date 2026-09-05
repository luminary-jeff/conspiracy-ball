# Conspiracy Ball — fantasy football assistant (Sleeper, read-only)

Jeff has almost no time this season. The contract: he runs ONE command, reads a short
recommendation, and makes the move in the Sleeper app — under 30 seconds on the draft
clock, under 60 seconds for anything in-season. This tool analyzes; it never acts.
Sleeper's public API is read-only, so the tool cannot set lineups, submit claims, or draft.

## League facts (verified live 2026-09-04 — do not re-ask)

| fact | value |
|---|---|
| league | "Conspiracy Ball", `league_id 1389361720291528704`, season 2026, 12 teams |
| me | username `unnecessary_roughness`, `user_id 1261006544535834624`, display Unnecessary_Roughness, team "UFO Pilot", `roster_id 4` |
| scoring | half-PPR (rec 0.5); pass_yd 0.04 (1 pt/25), pass_td 4, int -1, fum_lost -2, rush/rec_yd 0.1, all TD 6. ALWAYS pull `scoring_settings` live from `/league/<id>` for point math (cached in `data/league.json`) |
| roster | QB, RB, RB, WR, WR, TE, FLEX(RB/WR/TE), K, DEF + 6 BN + 1 IR = 15 draftable |
| draft | `draft_id 1389361720291528705`, 15-round snake, 60-second pick timer, no 3rd-round reversal, cpu autopick on. Draft is **2026-09-05 1:30 PM EST**. `draft_order` is null until the commissioner sets it; then it is `{user_id: slot}` |
| keepers | max 1; keepers show up in `/draft/<id>/picks` with `is_keeper: true` and are treated as drafted |
| waivers | **reverse-standings priority, NOT FAAB** (`waiver_type 1`; Jeff's original brief said FAAB — the API and the app disagree). `waiver_budget 100` is Sleeper's default and unused. Processes Wednesday morning (`waiver_day_of_week 2` = Tue night deadline), dropped players sit 2 days. Before the first kickoff every unowned player is an instant free-agent add |
| season | playoffs 6 teams starting week 15; trade deadline week 11; trade review 2 days; 6 veto votes |
| notes | two roster slots (6, 7) had no owner on 2026-09-04; user `HaalandGlobeTrotter` was in the league with no roster. `settings.draft_rounds` on the league object says 3 — ignore it, the draft object's `settings.rounds` (15) is authoritative |

## Sleeper API etiquette

- Base `https://api.sleeper.app/v1/`, no auth, read-only. Stay far below 1000 calls/min: the live
  draft loop is one call per 5s plus draft metadata every 30s.
- `GET /players/nfl` is ~15 MB — fetched at most once per 24h, cached at `data/players_nfl.json`.
- Every fetch goes through `Sleeper.get_cached()` in `ff/sleeper.py`: fresh cache → return; else
  fetch; on any error → stale cache with a warning → `default`. Nothing network-related raises.
- Undocumented endpoints (may change without notice; all wrapped defensively):
  - `GET https://api.sleeper.app/projections/nfl/<season>?season_type=regular&position[]=QB&...&order_by=adp_half_ppr`
    → season-long projections (RotoWire) with full stat lines, `pts_half_ppr`, and `adp_half_ppr` (999 = undrafted).
    Only ~630 players carry season points. K/DEF stat lines are partial, so we trust their `pts_half_ppr`.
  - `.../projections/nfl/<season>/<week>?season_type=regular&position[]=...` → weekly projections with `opponent`.
  - `.../stats/nfl/<season>/<week>?season_type=regular&position[]=...` → weekly actuals.
  - `.../schedule/nfl/regular/<season>` → games; bye weeks derived as "team absent in a week".
- Second ranking source: FantasyPros half-PPR ECR, parsed from the `var ecrData = {...}` blob embedded in
  `https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php` (needs a browser User-Agent).
  Gives `rank_ecr`, `tier`, `pos_rank`, `player_bye_week`, `rank_std`. ~936/946 rows match Sleeper ids by
  normalized name+position (aliases in `ff/board.py` `NAME_ALIASES`; FP uses `JAC` for `JAX`).
- Optional manual override: drop `data/manual_rankings.csv` (`name,pos,team,rank[,tier]`) and it replaces ECR.

## Value model (ff/board.py)

`value = 0.5 * VORP + 0.5 * consensus-implied VORP`, where VORP = league-scored projection minus the
projection at the replacement rank (`REPLACEMENT_RANK` in `ff/config.py`: QB16 RB40 WR42 TE16 K12 DEF12 —
about ten deeper than the last starter so bench rounds keep positive value; RotoWire RBs fall off a cliff after
RB40 so do not go deeper). Consensus-implied VORP = the VORP of whoever sits at the player's ECR rank on the
projection-only board. K/DEF use VORP only (consensus ranks them ~150-200 which drags them negative).
Tiers are gap-based on value per position (RB/WR gap ≥ max(6, 10%); QB/TE ≥ max(6, 12%)).

## Draft recommendation (ff/draft.py) — dynamic VBD

Objective: maximize the projected value of my final **starting lineup** (QB, 2RB, 2WR, TE, FLEX, K, DEF;
each starter counts blended-points minus replacement at his position, empty slot = 0), plus a small bench
credit (`BENCH_W`: RB/WR 0.15, QB/TE 0.05 of value) as injury/bye insurance.

For each candidate on my turn (top ~30 by value and by a fast need heuristic), `complete_roster()` greedily
fills every remaining pick of mine with the player maximizing marginal lineup gain x P(still available at
that pick) — P from a normal centred on ADP (sd = 3 + 0.16*ADP, ADP floored at the current pick so fallers
get re-priced). The candidate whose completed roster scores highest is the recommendation; the screen shows
the margin to the alternatives and the positional plan for the later picks. Hard rules: never more than
2 QB / 2 TE / 1 K / 1 DEF; K/DEF only in my last three picks; if picks left == unfilled starter slots, only
those positions. Runs in ~0.1s.

Why this and not "RB first" rules: value-based drafting is the mathematically grounded method, and the
popular strategies (RB-heavy, Zero RB, late QB, elite TE) are what it produces under particular projection
and ADP conditions — the roster-completion step computes that from this year's data instead of hardcoding it.
Validation (2026-09-04, `scratchpad eval.py`): 12 simulated drafts vs ADP-following opponents — my roster
ranked 1st in 8 of 12 slots, mean rank 1.75, ~+110 projected starter points over the average opponent.
In-sample caveat: the same projections are used to draft and to score.

## Command inventory

| command | what it does |
|---|---|
| `python3 ff.py draft-prep [--force]` | fetch player map, projections, ECR, byes, scoring → `data/board.json` + `data/board.txt` (overall + per-position tiers + tier-break timing). Run the night before and again ~1 hour pre-draft. ~2s warm, ~15s cold |
| `python3 ff.py draft-live [--slot N] [--interval 5]` | live screen; polls picks every 5s; my slot from `draft_order` (or `--slot`). Zero network on the clock beyond the poll. Ctrl-C to quit |
| `python3 ff.py draft-live --dry-run [--slot N] [--speed 0.35] [--pause 3]` | ~2-minute simulated draft vs ADP; opponents pick with ADP noise, my picks auto-take the recommendation |
| `python3 ff.py draft-live --once` | render a single frame and exit (debugging) |
| `python3 ff.py brief [--all]` | day-aware in-season brief. Tue: recap + waivers + trades + preview. Wed: waivers + trades + preview + lineup. Thu-Mon: lineup + preview. `--all` prints everything |
| `python3 ff.py lineup` | optimal starters vs what is set in Sleeper; lists OUT/IN moves only if the gain is >= 1 pt; Q/D re-check list with the fallback for each; early-lock (Thu/Fri) warning; ceiling tilt when win prob < 40%, floor tilt > 65% |
| `python3 ff.py preview` | opponent, both projected totals, win probability (normal, team sd 20), slot-by-slot edges, their injury exposure, and what happens if they fix a bad lineup |
| `python3 ff.py waivers` | roster moves: ADD NOW vs CLAIM, each with its drop (never the last backup QB/TE), IR moves, DEF stream only if a free agent out-ranks mine this week, upcoming byes (4 wks), league-wide hot pickups (Sleeper 48h add counts). FAAB bids only appear if `waiver_type == 2` |
| `python3 ff.py trades` | offers worth sending (my ROS lineup gain >= 4, theirs >= -2, raw value within 1.3x), one per partner; pending offers involving me |
| `python3 ff.py trade give Waddle for Nabers` | evaluate an explicit trade: ACCEPT / DECLINE / COIN FLIP |
| `python3 ff.py recap` | last week: my result, all scores, top/duds, bench regret, standings, 3 templated trash-talk lines |
| `python3 standings_draft.py [blend|fp|sleeper]` | rank all 12 rosters by lineup value under a chosen source |

In-season data (ff/season.py): Sleeper weekly projections (league-scored), FantasyPros weekly pages
(`half-point-ppr-flex.php`, `qb.php`, `k.php`, `dst.php` -> rank, pos_rank, start_sit_grade, opponent) and
`ros-half-point-ppr-overall.php` (rest-of-season board built with the draft blend), ESPN scoreboard
(`site.api.espn.com/.../scoreboard?week=N&seasontype=2&dates=<season>` -> kickoff times, spread, total,
implied team totals; **ESPN returns 403 to browser-like and custom User-Agents but accepts `curl/8.4.0`**),
Sleeper trending adds/drops (48h). Injury multipliers: Out/IR/PUP/Sus/NA 0, Doubtful 0.3, Questionable 0.9.
Weekly cadence that matters: waivers process Wed morning (submit Tue night); Sunday inactives drop ~90 min
before 1 PM ET kickoff; Thu/Fri games lock those players early; trade deadline week 11; playoffs weeks 15-17.

Env: `NO_COLOR=1` disables ANSI colour. Python 3.9 + `requests` only; no pandas, no database.

## Engineering rules for this repo

- Python 3.9 compatible (macOS system python): no `match`, no `X | Y` unions, no `list[str]` at runtime.
- Never raise on network failure; degrade to cached data and say so on screen.
- Output must be scannable in 30 seconds: bold single RECOMMENDATION + one reason line, then supporting tables.
- Keep `data/` as the only state; it is disposable and gitignored.
- When an undocumented endpoint changes shape, fix the wrapper in `ff/sleeper.py` and note it here.
