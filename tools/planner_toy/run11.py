"""Walks and worker occupancy: run11.py none|terran|protoss|zerg <limit>"""
import sys

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
WALKS = {"cc": (25.0, 25.0), "depot": (6.0, 6.0), "refinery": (5.0, 5.0), "barracks": (8.0, 8.0),
         "factory": (8.0, 8.0), "starport": (8.0, 8.0)}
BIG = {"cc", "barracks", "factory", "starport"}
race, limit = sys.argv[1], float(sys.argv[2])
grid = m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600))
kwargs = {"none": {}, "terran": dict(walks=WALKS), "protoss": dict(walks=WALKS, builder_during_build=False),
          "zerg": dict(walks={k: (v[0], 0.0) for k, v in WALKS.items()}, builder_during_build=False, builder_consumed=True)}[race]
model = m.build(grid, W, **kwargs)
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= (600 if item.name in BIG else 90):
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"{race:>8} builder, {limit:.0f}s", model, res, s, until=100)
