"""Geometric grids: run7.py <grid> <window_s> <limit>"""
import sys

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
GRIDS = {
    "step":     m.grid((2, 90), (30, 300), (60, 600)),
    "double60": m.grid((2, 60), (4, 120), (8, 240), (16, 480), (32, 600)),
    "double90": m.grid((2, 90), (4, 180), (8, 360), (16, 600)),
    "double45": m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600)),
}
name, window, limit = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
grid = GRIDS[name]
model = m.build(grid, W)
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= window:
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"{name} ({len(grid) - 1} buckets), integer to {window:.0f}s, {limit:.0f}s", model, res, s, until=100)
