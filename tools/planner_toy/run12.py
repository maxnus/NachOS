"""Attrition: run12.py <army loss rate per second> <worker loss rate per second> <limit>"""
import sys
from collections import Counter

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
BIG = {"cc", "barracks", "factory", "starport"}
army, workers, limit = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
grid = m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600))
attrition = {u: army for u in ("marine", "marauder", "medivac") if army} | ({"scv": workers} if workers else {})
model = m.build(grid, W, attrition=attrition)
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= (600 if item.name in BIG else 90):
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"army loss {army:.4f}/s, worker loss {workers:.4f}/s, {limit:.0f}s", model, res, s, until=100)
counts, order = m.describe(model, res)
per_minute = {u: Counter() for u in ("marine", "marauder", "barracks", "scv")}
for sec, unit, n in order:
    if unit in per_minute:
        per_minute[unit][int(sec // 60)] += n
for unit, minutes in per_minute.items():
    print(f"         {unit:>8} started per minute:", " ".join(f"{minutes[k]:3d}" for k in range(10)))
