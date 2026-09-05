"""League constants. Verified against the live Sleeper API on 2026-09-04."""
import os

LEAGUE_NAME = "Conspiracy Ball"
LEAGUE_ID = "1389361720291528704"
DRAFT_ID = "1389361720291528705"
SEASON = "2026"

MY_USER_ID = "1261006544535834624"
MY_USERNAME = "unnecessary_roughness"
MY_ROSTER_ID = 4          # from /league/<id>/rosters (owner_id == MY_USER_ID)

NUM_TEAMS = 12
ROUNDS = 15
PICK_TIMER = 60
STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
BENCH_SLOTS = 6
FLEX_ELIGIBLE = ("RB", "WR", "TE")
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

FAAB_BUDGET = 100
PLAYOFF_TEAMS = 6
PLAYOFF_START_WEEK = 15
TRADE_DEADLINE_WEEK = 11
MAX_KEEPERS = 1

# Value-over-replacement baselines: positional rank whose projection counts as
# "replacement level". Set ~10 deeper than the last starter (12-team, 1QB/2RB/2WR/
# 1TE/1FLEX) so bench-round picks still carry positive value (2026 RotoWire RB
# projections drop off a cliff after RB40, so don't go deeper than that).
REPLACEMENT_RANK = {"QB": 16, "RB": 40, "WR": 42, "TE": 16, "K": 12, "DEF": 12}

# Typical full-roster targets over 15 rounds (used for need weighting only).
ROSTER_TARGET = {"QB": 2, "RB": 6, "WR": 6, "TE": 2, "K": 1, "DEF": 1}

API_BASE = "https://api.sleeper.app/v1"
API_ROOT = "https://api.sleeper.app"

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
