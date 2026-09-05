"""Compute fantasy points from a Sleeper stat line using the league's scoring_settings."""


def compute_points(stats, scoring):
    if not stats:
        return 0.0
    total = 0.0
    for k, v in stats.items():
        w = scoring.get(k)
        if w and v:
            try:
                total += float(v) * float(w)
            except (TypeError, ValueError):
                pass
    return round(total, 2)


def league_points(stats, scoring, pos):
    """League-scored points. K/DEF stat lines from Sleeper are partial, so trust
    their pre-computed pts_half_ppr there; for skill positions compute from the
    stat line and fall back to pts_half_ppr if the line is missing/implausible."""
    stats = stats or {}
    provided = stats.get("pts_half_ppr")
    if pos in ("K", "DEF"):
        return float(provided or 0.0)
    pts = compute_points(stats, scoring)
    if provided and (pts <= 0 or pts < 0.6 * float(provided)):
        return float(provided)
    return pts
