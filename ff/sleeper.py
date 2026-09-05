"""Thin, defensive Sleeper API client with disk caching.

Every fetch goes through get_cached(): fresh cache -> return; else fetch; on any
error fall back to stale cache (with a warning) or to `default`.
"""
import json
import os
import time

import requests

from . import config
from .util import warn

HEADERS = {"User-Agent": "conspiracy-ball-assistant/1.0 (read-only; contact: league member)"}


class Sleeper:
    def __init__(self, data_dir=config.DATA_DIR, timeout=20):
        self.data_dir = data_dir
        self.timeout = timeout
        os.makedirs(data_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.calls = 0

    # ---- low level -------------------------------------------------------
    def _get(self, url, params=None):
        self.calls += 1
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _path(self, name):
        return os.path.join(self.data_dir, name)

    def read_cache(self, name):
        path = self._path(name)
        if not os.path.exists(path):
            return None, None
        try:
            with open(path) as f:
                blob = json.load(f)
            return blob.get("data"), blob.get("fetched_at")
        except Exception as e:  # corrupt cache
            warn("cache %s unreadable (%s)" % (name, e))
            return None, None

    def write_cache(self, name, data):
        tmp = self._path(name + ".tmp")
        with open(tmp, "w") as f:
            json.dump({"fetched_at": time.time(), "data": data}, f)
        os.replace(tmp, self._path(name))

    def get_cached(self, url, name, max_age, params=None, default=None, force=False):
        """Return (data, fetched_at). Never raises; degrades to stale cache or default."""
        data, ts = self.read_cache(name)
        if data is not None and not force and ts and time.time() - ts < max_age:
            return data, ts
        try:
            fresh = self._get(url, params)
            if fresh is None or fresh == [] or fresh == {}:
                raise ValueError("empty response")
            self.write_cache(name, fresh)
            return fresh, time.time()
        except Exception as e:
            if data is not None:
                warn("%s failed (%s); using cached copy from %s" % (name, e, time.ctime(ts or 0)))
                return data, ts
            warn("%s failed (%s); no cache available" % (name, e))
            return default, None

    def get_live(self, url, params=None, default=None):
        """Uncached live call; returns default on error (never raises)."""
        try:
            return self._get(url, params)
        except Exception as e:
            warn("live call %s failed: %s" % (url, e))
            return default

    # ---- documented endpoints -------------------------------------------
    def state(self):
        return self.get_cached("%s/state/nfl" % config.API_BASE, "state.json", 3600, default={})[0]

    def league(self, force=False):
        return self.get_cached("%s/league/%s" % (config.API_BASE, config.LEAGUE_ID),
                               "league.json", 6 * 3600, default={}, force=force)[0]

    def users(self):
        return self.get_cached("%s/league/%s/users" % (config.API_BASE, config.LEAGUE_ID),
                               "users.json", 6 * 3600, default=[])[0]

    def rosters(self, force=False):
        return self.get_cached("%s/league/%s/rosters" % (config.API_BASE, config.LEAGUE_ID),
                               "rosters.json", 3600, default=[], force=force)[0]

    def matchups(self, week):
        return self.get_live("%s/league/%s/matchups/%s" % (config.API_BASE, config.LEAGUE_ID, week), default=[])

    def transactions(self, week):
        return self.get_live("%s/league/%s/transactions/%s" % (config.API_BASE, config.LEAGUE_ID, week), default=[])

    def draft(self, live=False):
        url = "%s/draft/%s" % (config.API_BASE, config.DRAFT_ID)
        if live:
            d = self.get_live(url)
            if d:
                self.write_cache("draft.json", d)
                return d
            return self.read_cache("draft.json")[0] or {}
        return self.get_cached(url, "draft.json", 300, default={})[0]

    def draft_picks(self):
        """Live picks; returns None (not []) on failure so callers can keep last state."""
        return self.get_live("%s/draft/%s/picks" % (config.API_BASE, config.DRAFT_ID), default=None)

    def players(self):
        """~15MB player map; refreshed at most once per day."""
        return self.get_cached("%s/players/nfl" % config.API_BASE, "players_nfl.json", 24 * 3600, default={})

    def trending(self, kind="add", hours=24, limit=50):
        return self.get_cached("%s/players/nfl/trending/%s" % (config.API_BASE, kind),
                               "trending_%s.json" % kind, 1800,
                               params={"lookback_hours": hours, "limit": limit}, default=[])[0]

    # ---- undocumented endpoints (wrapped; may vanish) ---------------------
    def projections_season(self, season=config.SEASON, force=False):
        url = "%s/projections/nfl/%s" % (config.API_ROOT, season)
        params = {"season_type": "regular", "order_by": "adp_half_ppr",
                  "position[]": ["QB", "RB", "WR", "TE", "K", "DEF"]}
        return self.get_cached(url, "projections_%s_season.json" % season, 6 * 3600,
                               params=params, default=[], force=force)

    def projections_week(self, week, season=config.SEASON):
        url = "%s/projections/nfl/%s/%s" % (config.API_ROOT, season, week)
        params = {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE", "K", "DEF"]}
        return self.get_cached(url, "projections_%s_wk%s.json" % (season, week), 3 * 3600,
                               params=params, default=[])

    def stats_week(self, week, season=config.SEASON):
        url = "%s/stats/nfl/%s/%s" % (config.API_ROOT, season, week)
        params = {"season_type": "regular", "position[]": ["QB", "RB", "WR", "TE", "K", "DEF"]}
        return self.get_cached(url, "stats_%s_wk%s.json" % (season, week), 3600,
                               params=params, default=[])

    def schedule(self, season=config.SEASON):
        url = "%s/schedule/nfl/regular/%s" % (config.API_ROOT, season)
        return self.get_cached(url, "schedule_%s.json" % season, 7 * 24 * 3600, default=[])


def bye_weeks(schedule):
    """Derive {team: bye_week} from the schedule list (teams absent in a week)."""
    weeks = {}
    teams = set()
    for g in schedule or []:
        w = g.get("week")
        if not w or w > 18:
            continue
        weeks.setdefault(w, set()).update([g.get("home"), g.get("away")])
        teams.update([g.get("home"), g.get("away")])
    teams.discard(None)
    byes = {}
    for w, playing in weeks.items():
        for t in teams - playing:
            byes.setdefault(t, w)
    return byes
