#!/usr/bin/env python3
"""Rank all 12 draft rosters by projected lineup value (same formula as draft-live)."""
import json, warnings
warnings.filterwarnings("ignore")
import sys
import requests
from ff.draft import lineup_value
from ff import config

SOURCE = (sys.argv[1] if len(sys.argv) > 1 else "blend").lower()   # blend | fp | sleeper
players = json.load(open('data/board.json'))['players']
if SOURCE != "blend":
    # value curve: VORP at each overall rank on the projection-only board
    curve = sorted([p['vorp'] for p in players if p.get('vorp') is not None], reverse=True)
    for p in players:
        if SOURCE == "fp":
            p['value'] = curve[min(len(curve) - 1, p['ecr'] - 1)] if p.get('ecr') else 0.0
        elif SOURCE == "sleeper":
            p['value'] = p['vorp'] if p.get('vorp') is not None else 0.0
b = {p['player_id']: p for p in players}
users = {u['user_id']: u.get('display_name') for u in json.load(open('data/users.json'))['data']}
d = requests.get('https://api.sleeper.app/v1/draft/%s' % config.DRAFT_ID, timeout=20).json()
slot_user = {int(s): users.get(u, u) for u, s in d['draft_order'].items()}
picks = requests.get('https://api.sleeper.app/v1/draft/%s/picks' % config.DRAFT_ID, timeout=20).json()
rosters = {}
for p in picks:
    rosters.setdefault(p['draft_slot'], []).append(
        b.get(p['player_id'], {'pos': p['metadata'].get('position'), 'value': 0, 'pts': 0, 'name': p['metadata'].get('last_name')}))
print('picks so far: %d / %d   (draft %s)   source: %s' % (len(picks), config.NUM_TEAMS * config.ROUNDS, d.get('status'),
      {"blend": "50/50 Sleeper projections + FantasyPros ECR", "fp": "FantasyPros ECR only", "sleeper": "Sleeper/RotoWire projections only"}[SOURCE]))
rows = sorted(((lineup_value(r), s) for s, r in rosters.items()), reverse=True)
print('%-4s %-20s %7s  %s' % ('rank', 'team', 'lineup', 'roster'))
for i, (lv, s) in enumerate(rows, 1):
    r = rosters[s]
    ro = ', '.join('%s %s' % (e['pos'], (e.get('name') or '?').split()[-1]) for e in r)
    print('%-4d %-20s %7.0f  %s' % (i, ('* ' if s == 1 else '') + str(slot_user.get(s))[:18], lv, ro))
