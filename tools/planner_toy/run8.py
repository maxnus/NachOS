"""Per-item integrality windows: run8.py <units_s> <small_structures_s> <big_structures_s> <limit> [half_life]"""
import sys

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
UNITS = {"scv", "marine", "marauder", "medivac"}
SMALL = {"depot", "refinery", "techlab", "reactor", "orbital", "stim"}
BIG = {"cc", "barracks", "factory", "starport"}
units, small, big, limit = (float(a) for a in sys.argv[1:5])
hl = float(sys.argv[5]) if len(sys.argv) > 5 else None
grid = m.grid((2, 45), (4, 90), (8, 180), (16, 360), (32, 600))
model = m.build(grid, W, half_life=hl)
for item in m.ITEMS:
    window = units if item.name in UNITS else small if item.name in SMALL else big
    for t in range(model["T"]):
        if model["edges"][t] >= window:
            model["integrality"][model["x"](item.name, t)] = 0
res, s = m.solve(model, time_limit=limit)
counts, order = m.describe(model, res)
structures = ", ".join(f"{sec:.0f}s {name}" + (f"x{n}" if n > 1 else "")
                       for sec, name, n in order if name in BIG and sec >= 90)
m.report(f"units {units:.0f}s / small {small:.0f}s / big {big:.0f}s, {limit:.0f}s", model, res, s, until=90)
print("         big structures after 90s:", structures or "none whole")
