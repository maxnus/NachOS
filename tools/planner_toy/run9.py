"""Linear against concave rewards: run9.py linear|concave <limit>"""
import sys

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
# Diminishing returns: the first 30 marines at full weight, the next 30 at a third, then nothing; likewise the others.
CURVES = {"marine": [(1.0, 30), (0.33, 60), (0.0, 999)],
          "marauder": [(2.2, 12), (0.7, 30), (0.0, 999)],
          "medivac": [(2.5, 4), (0.8, 8), (0.0, 999)]}
BIG = {"cc", "barracks", "factory", "starport"}
mode, limit = sys.argv[1], float(sys.argv[2])
grid = m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600))
model = m.build(grid, W, curves=CURVES if mode == "concave" else None)
for item in m.ITEMS:
    for t in range(model["T"]):
        if model["edges"][t] >= (600 if item.name in BIG else 90):
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
m.report(f"{mode} reward, {limit:.0f}s", model, res, s, until=90)
