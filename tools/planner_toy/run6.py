"""Finer buckets with a longer integer window and a discount: run6.py <near_s> <window_s> <limit> [half_life]"""
import sys
import time

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
near, window, limit = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
hl = float(sys.argv[4]) if len(sys.argv) > 4 else None
grid = m.grid((near, window), (30, 300), (60, 600)) if window < 300 else m.grid((near, window), (60, 600))
t0 = time.perf_counter()
model = m.build(grid, W, half_life=hl)
built = time.perf_counter() - t0
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= window:
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"{near:.0f}s buckets, integer to {window:.0f}s, half-life {hl}, {limit:.0f}s", model, res, s, until=120)
