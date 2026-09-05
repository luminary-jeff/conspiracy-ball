# Conspiracy Ball assistant

Read-only Sleeper draft/in-season helper. Full context in `CLAUDE.md`.

## Draft day

```bash
cd ~/Projects/fantasy_football
python3 ff.py draft-prep            # night before + ~1h before the draft (refreshes ADP/ECR/injuries)
python3 ff.py draft-live            # open ~10 min before 1:30 PM; leave it running next to the Sleeper app
```

While `draft-live` runs it polls Sleeper every 5 seconds and shows: pick clock, my roster slots,
one bold **PICK NOW** recommendation with a one-line reason, 3 alternates with the odds they last
until my following pick, top 10 available, best available per position with tier-break warnings,
positional-run alerts, and who is likely gone before my next turn.

If the commissioner has not set the draft order yet the screen says so and keeps re-checking; pass
`--slot N` to force a slot. `NO_COLOR=1` for plain text.

Dry run (about two minutes, no network): `python3 ff.py draft-live --dry-run`

## In-season

```bash
python3 ff.py brief            # the right sections for today (Tue: recap/waivers/trades; game days: lineup/preview)
python3 ff.py brief --all      # everything
python3 ff.py lineup | preview | waivers | trades | recap
python3 ff.py trade give Waddle for Nabers
python3 standings_draft.py     # all 12 rosters ranked by projected lineup value
```

Three runs a week cover the season: Tuesday evening (waivers, trades, recap), Thursday noon ET if you
start a Thursday-night player, and Sunday ~11:45 AM ET after inactives are announced (lineup).
