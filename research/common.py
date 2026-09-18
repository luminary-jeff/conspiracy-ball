"""Shared paths, loaders and small stats helpers for the projection-accuracy study.

Python 3.9, numpy + stdlib + requests only. Reads the tool's modules (ff.sleeper, ff.scoring) but the
tool never imports research/.
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RESEARCH_DIR = os.path.join(ROOT, "research")
DATA_DIR = os.path.join(RESEARCH_DIR, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
SNAP_DIR = os.path.join(RESEARCH_DIR, "snapshots")
DATASET = os.path.join(DATA_DIR, "dataset.csv")
DEFENSE = os.path.join(DATA_DIR, "defense.csv")
GAMES = os.path.join(DATA_DIR, "games.csv")

POSITIONS = ("QB", "RB", "WR", "TE")
HIST_SEASONS = (2022, 2023, 2024, 2025)
LIVE_SEASON = 2026

# Sleeper team code -> nflverse team code (only the ones that differ)
TO_NFLVERSE = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LA"}


def nv_team(t):
    return TO_NFLVERSE.get(t, t)


def fnum(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, cols):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def load_scoring():
    """League scoring settings: the tool's cache if present, else one live call through the tool's client."""
    path = os.path.join(ROOT, "data", "league.json")
    if os.path.exists(path):
        with open(path) as f:
            blob = json.load(f)
        s = (blob.get("data") or {}).get("scoring_settings")
        if s:
            return s
    from ff.sleeper import Sleeper
    return (Sleeper().league() or {}).get("scoring_settings") or {}
