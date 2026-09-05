import re
import sys
import time

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# FantasyPros -> Sleeper team abbreviations
TEAM_ALIASES = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def norm_team(t):
    if not t:
        return None
    t = t.upper()
    return TEAM_ALIASES.get(t, t)


def norm_name(name):
    """'Amon-Ra St. Brown' -> 'amonrastbrown'; 'Kenneth Walker III' -> 'kennethwalker'."""
    if not name:
        return ""
    s = name.lower().replace("-", " ").replace("'", "").replace(".", "")
    s = re.sub(r"[^a-z ]", "", s)
    return "".join(t for t in s.split() if t not in _SUFFIXES)


def warn(msg):
    sys.stderr.write("[warn] %s\n" % msg)
    sys.stderr.flush()


def age_str(ts):
    if not ts:
        return "never"
    d = time.time() - ts
    if d < 90:
        return "%ds ago" % d
    if d < 5400:
        return "%dm ago" % (d / 60)
    if d < 172800:
        return "%.1fh ago" % (d / 3600)
    return "%.1fd ago" % (d / 86400)
