import sys
import toy_milp as m
W = {"marine": 1.0, "marauder": 2.2, "medivac": 2.5}
case = sys.argv[1]
if case == "check":
    for share in (0.0, 0.5):
        model = m.build(m.grid((10, 420)), W, spend_share=share)
        res, s = m.solve(model, time_limit=3)
        m.report(f"7 min uniform 10s share={share} 3s", model, res, s)
elif case == "grid":
    name, limit = sys.argv[2], float(sys.argv[3])
    grids = {"u10": m.grid((10, 600)), "u20": m.grid((20, 600)), "w10-20-30": m.grid((10, 120), (20, 300), (30, 600)),
             "w10-30-60": m.grid((10, 90), (30, 300), (60, 600))}
    model = m.build(grids[name], W)
    res, s = m.solve(model, time_limit=limit)
    m.report(f"10 min {name} {limit:.0f}s", model, res, s, until=120)
elif case == "discount":
    hl = None if sys.argv[2] == "none" else float(sys.argv[2])
    model = m.build(m.grid((10, 420)), W, half_life=hl)
    res, s = m.solve(model, time_limit=10)
    m.report(f"7 min half-life {sys.argv[2]}s", model, res, s, until=150)
elif case == "combo":
    name, hl, limit = sys.argv[2], sys.argv[3], float(sys.argv[4])
    grids = {"u10": m.grid((10, 600)), "u20": m.grid((20, 600)), "w10-30-60": m.grid((10, 90), (30, 300), (60, 600))}
    model = m.build(grids[name], W, half_life=None if hl == "none" else float(hl))
    res, s = m.solve(model, time_limit=limit)
    m.report(f"10 min {name} half-life {hl} {limit:.0f}s", model, res, s, until=120)
