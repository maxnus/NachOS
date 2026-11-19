"""The enemy's composition in the reward: run10.py <scenario> <limit>"""
import sys
from collections import Counter

import toy_milp as m

# A small standing value per unit, and the real reward from covering the enemy.
W = {"marine": 0.1, "marauder": 0.2, "medivac": 0.1}
EFFECTIVENESS = {"marine": {"air": 1.0, "armored": 0.3, "light": 0.8},
                 "marauder": {"air": 0.0, "armored": 1.0, "light": 0.3}}
VALUE = {"air": 1.0, "armored": 1.0, "light": 1.0}
SCENARIOS = {
    "armored": {"armored": lambda s: 30, "light": lambda s: 10, "air": lambda s: 0},
    "air-at-4:00": {"armored": lambda s: 30, "light": lambda s: 10, "air": lambda s: 25 if s >= 240 else 0},
    "air": {"armored": lambda s: 0, "light": lambda s: 10, "air": lambda s: 25},
}
BIG = {"cc", "barracks", "factory", "starport"}
name, limit = sys.argv[1], float(sys.argv[2])
grid = m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600))
model = m.build(grid, W, coverage=(EFFECTIVENESS, SCENARIOS[name], VALUE))
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= (600 if item.name in BIG else 90):
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"enemy {name}, {limit:.0f}s", model, res, s)
counts, order = m.describe(model, res)
by_minute = {u: Counter() for u in ("marine", "marauder")}
for sec, unit, n in order:
    if unit in by_minute:
        by_minute[unit][int(sec // 60)] += n
for unit, minutes in by_minute.items():
    print(f"         {unit:>8} started per minute:", " ".join(f"{minutes[k]:3d}" for k in range(10)))
