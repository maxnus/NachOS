"""Where the time goes, and a fluid far term: run3.py build | relax <integer_until_s> <half_life|none> <limit>"""
import sys
import time

import numpy as np

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
G = m.grid((10, 90), (30, 300), (60, 600))
if sys.argv[1] == "build":
    for grid, label in ((m.grid((10, 420)), "7 min uniform 10 s"), (G, "10 min widening"), (m.grid((10, 600)), "10 min uniform 10 s")):
        t0 = time.perf_counter(); model = m.build(grid, W); built = time.perf_counter() - t0
        t0 = time.perf_counter(); res, _ = m.solve(model, time_limit=0.05); first = time.perf_counter() - t0
        print(f"{label:>20}: build {built*1000:5.0f} ms, rows={model['A'].shape[0]} nnz={model['A'].nnz}; solve with a 50 ms limit took {first*1000:5.0f} ms, status={res.status}, incumbent={'yes' if res.x is not None else 'no'}")
else:
    until, hl, limit = float(sys.argv[2]), sys.argv[3], float(sys.argv[4])
    model = m.build(G, W, half_life=None if hl == "none" else float(hl))
    for item in m.ITEMS:
        for t in range(model["T"]):
            if model["edges"][t] >= until:
                model["integrality"][model["x"](item.name, t)] = 0
    res, s = m.solve(model, time_limit=limit)
    m.report(f"integer to {until:.0f}s, half-life {hl}, {limit:.0f}s", model, res, s, until=90)
