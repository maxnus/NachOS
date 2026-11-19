"""Everything fluid: the LP relaxation's near term, and what rounding it does."""
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

import toy_milp as m

W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
G = m.grid((10, 90), (30, 300), (60, 600))
UNTIL = 90.0


def near_term(model, x_values, *, threshold=0.005):
    edges, T, x = model["edges"], model["T"], model["x"]
    out = []
    for t in range(T):
        if edges[t] >= UNTIL:
            break
        for item in m.ITEMS:
            v = x_values[x(item.name, t)]
            if v > threshold:
                out.append(f"{edges[t]:.0f}s {item.name} {v:.2f}")
    return ", ".join(out)


def solve_with(model, integrality, bounds, limit=10.0):
    t0 = time.perf_counter()
    res = milp(model["c"], constraints=LinearConstraint(model["A"], model["lo"], model["hi"]), bounds=bounds,
               integrality=integrality, options={"time_limit": limit, "mip_rel_gap": 1e-4, "disp": False})
    return res, time.perf_counter() - t0


model = m.build(G, W)
T, x, edges = model["T"], model["x"], model["edges"]
n = len(model["c"])
relaxed = np.zeros(n)

# 1. Everything fluid
res_lp, s = solve_with(model, relaxed, model["bounds"])
print(f"all fluid LP: {s*1000:.0f} ms, objective {-res_lp.fun:.1f}")
print("   near term:", near_term(model, res_lp.x))

# 2. The mixed model, integer to 90 s, as the reference
integ = np.zeros(n)
for item in m.ITEMS:
    for t in range(T):
        if edges[t] < UNTIL:
            integ[x(item.name, t)] = 1
res_mix, s = solve_with(model, integ, model["bounds"])
print(f"integer to {UNTIL:.0f}s: {s:.2f} s, objective {-res_mix.fun:.1f}, gap {res_mix.mip_gap:.1e}")
print("   near term:", near_term(model, res_mix.x))

# 3. Round the LP's near term and fix it, then re-solve the rest as an LP
for name, rounder in (("nearest", np.round), ("down", np.floor), ("up", np.ceil)):
    lower = model["bounds"].lb.copy(); upper = model["bounds"].ub.copy()
    for item in m.ITEMS:
        for t in range(T):
            if edges[t] < UNTIL:
                v = float(rounder(res_lp.x[x(item.name, t)] + (1e-6 if name == "down" else 0)))
                lower[x(item.name, t)] = upper[x(item.name, t)] = v
    res, s = solve_with(model, relaxed, Bounds(lower, upper))
    if res.x is None:
        print(f"LP near term rounded {name:>7}: infeasible")
    else:
        print(f"LP near term rounded {name:>7}: objective {-res.fun:.1f}, that is {100 * (-res.fun) / (-res_mix.fun):.1f}% of the mixed model's")
        print("   near term:", near_term(model, res.x))
