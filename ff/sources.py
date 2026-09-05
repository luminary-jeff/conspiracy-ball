"""Non-Sleeper data sources. All defensive: return cached/None rather than raise."""
import json
import os
import re
import time

import requests

from . import config
from .util import warn

FP_URLS = {
    "half": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php",
    "ros_half": "https://www.fantasypros.com/nfl/rankings/ros-half-point-ppr-overall.php",
}
FP_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _cache_path(name):
    return os.path.join(config.DATA_DIR, name)


def fetch_fantasypros(kind="half", max_age=6 * 3600, force=False):
    """Expert consensus rankings (ECR) embedded as `var ecrData = {...}` in the page.
    Returns (players_list, fetched_at) or ([], None). Each player carries
    player_name, player_team_id, player_position_id, rank_ecr, tier, player_bye_week..."""
    name = "fp_ecr_%s.json" % kind
    path = _cache_path(name)
    cached, ts = None, None
    if os.path.exists(path):
        try:
            with open(path) as f:
                blob = json.load(f)
            cached, ts = blob.get("data"), blob.get("fetched_at")
            if cached and not force and time.time() - ts < max_age:
                return cached, ts
        except Exception:
            pass
    try:
        r = requests.get(FP_URLS[kind], headers=FP_HEADERS, timeout=25)
        r.raise_for_status()
        m = re.search(r"var ecrData = (\{.*?\});\s*\n", r.text, re.S)
        if not m:
            raise ValueError("ecrData blob not found (page layout changed?)")
        data = json.loads(m.group(1))
        players = data.get("players") or []
        if len(players) < 100:
            raise ValueError("too few players (%d)" % len(players))
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time(), "data": players,
                       "meta": {"last_updated": data.get("last_updated"),
                                "total_experts": data.get("total_experts")}}, f)
        return players, time.time()
    except Exception as e:
        if cached:
            warn("FantasyPros fetch failed (%s); using cache from %s" % (e, time.ctime(ts)))
            return cached, ts
        warn("FantasyPros fetch failed (%s); no cache. Board will use Sleeper projections only." % e)
        return [], None


def load_manual_csv(path=None):
    """Optional manual override/supplement: data/manual_rankings.csv with columns
    name,pos,team,rank[,tier][,note]. Lets you drop in any cheat sheet."""
    path = path or _cache_path("manual_rankings.csv")
    if not os.path.exists(path):
        return []
    import csv
    rows = []
    try:
        with open(path) as f:
            for row in csv.DictReader(f):
                if row.get("name") and row.get("rank"):
                    rows.append(row)
    except Exception as e:
        warn("manual_rankings.csv unreadable: %s" % e)
    return rows
