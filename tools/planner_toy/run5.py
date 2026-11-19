"""Finer near-term buckets: run5.py <grid> <limit>"""
import sys
import time

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
GRIDS = {
    "10s": m.grid((10, 90), (30, 300), (60, 600)),
    "5s": m.grid((5, 90), (30, 300), (60, 600)),
    "2s": m.grid((2, 90), (30, 300), (60, 600)),
    "1s": m.grid((1, 90), (30, 300), (60, 600)),
    "1s-to-30": m.grid((1, 30), (5, 90), (30, 300), (60, 600)),
}
name, limit = sys.argv[1], float(sys.argv[2])
t0 = time.perf_counter()
model = m.build(GRIDS[name], W)
built = time.perf_counter() - t0
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= 90:
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
print(f"build {built:.2f}s rows={model['A'].shape[0]} nnz={model['A'].nnz}")
m.report(f"near term {name}, {limit:.0f}s", model, res, s, until=90)
