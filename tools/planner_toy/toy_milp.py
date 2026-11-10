"""The toy Terran MILP on an arbitrary time grid, with an exponentially discounted reward.

`edges` are the bucket boundaries in seconds. An item started in bucket t starts at the bucket's first second, pays
from the stock at that moment plus `spend_share` of the bucket's income, occupies its producer for its build time
across however many buckets that spans (producer-seconds, so a long bucket holds several short builds), and counts as
done from the first bucket edge at or after its completion. The reward over a bucket is the counts done at its end
times the integral of exp(-lambda s) over the bucket, with lambda = ln 2 / half_life.
"""

import math
import sys
import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

R_MINE = 1.068
R_MINE2 = 0.5 * R_MINE
R_GAS = 2.728 / 3
R_MULE = 3.5
FIELDS = 8
MAX_BASES = 4
BIG = 20


@dataclass(frozen=True)
class Item:
    name: str
    minerals: float
    gas: float
    seconds: float
    supply: float = 0.0
    provides: float = 0.0


ITEMS = [
    Item("scv", 50, 0, 12, supply=1),
    Item("depot", 100, 0, 21, provides=8),
    Item("cc", 400, 0, 71, provides=15),
    Item("orbital", 150, 0, 25),
    Item("refinery", 75, 0, 21),
    Item("barracks", 150, 0, 46),
    Item("techlab", 50, 25, 18),
    Item("reactor", 50, 50, 36),
    Item("factory", 150, 100, 43),
    Item("starport", 150, 100, 36),
    Item("marine", 50, 0, 18, supply=1),
    Item("marauder", 100, 25, 21, supply=2),
    Item("medivac", 100, 100, 30, supply=2),
    Item("stim", 100, 100, 100),
]
A = {item.name: i for i, item in enumerate(ITEMS)}
SECONDS = {item.name: item.seconds for item in ITEMS}
BUILT_BY_SCV = ["depot", "cc", "refinery", "barracks", "factory", "starport"]
INITIAL = {"scv": 12, "cc": 1}


def grid(*segments: tuple[float, float]) -> list[float]:
    """Bucket edges from (bucket seconds, until second) segments: grid((10, 120), (20, 300), (30, 600))."""
    edges = [0.0]
    for width, until in segments:
        while edges[-1] + width <= until + 1e-9:
            edges.append(edges[-1] + width)
    return edges


def build(edges, weights: dict[str, float], *, terminal: bool = False, half_life: float | None = None,
          spend_share: float = 0.5, curves: dict[str, list[tuple[float, float]]] | None = None,
          coverage: tuple[dict[str, dict[str, float]], dict[str, object], dict[str, float]] | None = None,
          walks: dict[str, tuple[float, float]] | None = None, builder_during_build: bool = True,
          builder_consumed: bool = False, attrition: dict[str, float] | None = None):
    """`curves` maps a type to the segments of a concave piecewise-linear reward on its count, as (slope, count the
    segment runs to), the last count ignored; such a type's `weights` entry is unused. `coverage` rewards covering
    the enemy's units: (effectiveness[own type][enemy type], enemy[enemy type] as a function of seconds giving the
    count expected then, value[enemy type] per enemy unit covered), each enemy type covered at most to its count.
    `walks` gives a structure's (seconds to walk there, seconds to walk back): the worker is away for the walk there
    before the start, for the build itself when `builder_during_build` (Terran), and for the walk back after; a
    structure cannot start before its walk there unless a worker is already on its way. With `builder_consumed`
    (Zerg) the worker is gone from the start on. `attrition` gives a unit type's loss rate per second: a unit done
    survives with probability exp(-rate * seconds since), and only survivors count for income, supply, reward and
    coverage."""
    edges = np.asarray(edges, dtype=float)
    T = len(edges) - 1
    width = np.diff(edges)
    n_items = len(ITEMS)
    lam = math.log(2) / half_life if half_life else 0.0
    disc = ((np.exp(-lam * edges[:-1]) - np.exp(-lam * edges[1:])) / lam) if lam else width.copy()

    X0 = 0
    M0 = X0 + n_items * T
    G0 = M0 + T + 1
    U1 = G0 + T + 1
    U2 = U1 + T
    C0 = U2 + T
    curves = curves or {}
    curve_types = list(curves)
    R0 = C0 + T
    effectiveness, enemy, enemy_value = coverage if coverage else ({}, {}, {})
    enemy_types = list(enemy)
    V0 = R0 + len(curve_types) * T
    n_vars = V0 + len(enemy_types) * T

    def r(a: str, t: int) -> int:
        return R0 + curve_types.index(a) * T + t

    def cov_var(a: str, t: int) -> int:
        return V0 + enemy_types.index(a) * T + t

    def x(a: str, t: int) -> int:
        return X0 + A[a] * T + t

    def started_by(a: str, t: int, expr: dict, coef: float = 1.0) -> float:
        for tau in range(0, t + 1):
            expr[x(a, tau)] = expr.get(x(a, tau), 0.0) + coef
        return coef * INITIAL.get(a, 0)

    def done_by(a: str, t: int, expr: dict, coef: float = 1.0) -> float:
        """Items of `a` done by edge t."""
        for tau in range(0, t):
            if edges[tau] + SECONDS[a] <= edges[t] + 1e-9:
                expr[x(a, tau)] = expr.get(x(a, tau), 0.0) + coef
        return coef * INITIAL.get(a, 0)

    def busy_seconds(a: str, t: int, expr: dict, coef: float = 1.0, *, before: float = 0.0, during: float | None = None,
                     after: float = 0.0) -> None:
        """Seconds of bucket t that items of `a` occupy their producer, per start: from `before` seconds ahead of
        the start to `after` seconds past the end of `during`, the build time unless given."""
        length = SECONDS[a] if during is None else during
        for tau in range(0, T):
            overlap = min(edges[tau] + length + after, edges[t + 1]) - max(edges[tau] - before, edges[t])
            if overlap > 1e-9:
                expr[x(a, tau)] = expr.get(x(a, tau), 0.0) + coef * overlap

    walks = walks or {}

    def builder_seconds(s: str, t: int, expr: dict, coef: float) -> None:
        there, back = walks.get(s, (0.0, 0.0))
        busy_seconds(s, t, expr, coef, before=there, during=None if builder_during_build else 0.0, after=back)

    attrition = attrition or {}

    def alive_by(a: str, t: int, expr: dict, coef: float = 1.0) -> float:
        """Items of `a` done by edge t and still alive then; returns the constant part."""
        rate = attrition.get(a, 0.0)
        if not rate:
            return done_by(a, t, expr, coef)
        for tau in range(0, t):
            finished = edges[tau] + SECONDS[a]
            if finished <= edges[t] + 1e-9:
                survival = math.exp(-rate * (edges[t] - finished))
                expr[x(a, tau)] = expr.get(x(a, tau), 0.0) + coef * survival
        return coef * INITIAL.get(a, 0) * math.exp(-rate * edges[t])

    def in_progress(a: str, t: int, expr: dict, coef: float = 1.0) -> None:
        """Starts of `a` that occupy their producer at some moment of bucket t."""
        for tau in range(0, t + 1):
            if min(edges[tau] + SECONDS[a], edges[t + 1]) - max(edges[tau], edges[t]) > 1e-9:
                expr[x(a, tau)] = expr.get(x(a, tau), 0.0) + coef

    rows, cols, vals, lo, hi = [], [], [], [], []

    def capacity(t: int, busy: list[tuple[str, float]], cap) -> None:
        """busy items' producer-seconds <= capacity seconds, and where the bucket is shorter than every build in it,
        the count form too, which is tighter for the relaxation: `cap(expr, scale)` fills in the capacity."""
        w = width[t]
        e = {}
        for a, coef in busy:
            busy_seconds(a, t, e, coef)
        k = cap(e, -w)
        add(e, k, -np.inf, 0)
        if w <= min(SECONDS[a] for a, _ in busy) + 1e-9:
            e = {}
            for a, coef in busy:
                in_progress(a, t, e, coef)
            k = cap(e, -1.0)
            add(e, k, -np.inf, 0)

    def add(expr: dict, const: float, lower: float, upper: float) -> None:
        r = len(lo)
        for v, c in expr.items():
            if c != 0.0:
                rows.append(r); cols.append(v); vals.append(c)
        lo.append(lower - const)
        hi.append(upper - const)

    for t in range(T):
        w = width[t]
        # Mineral and gas flows
        e = {M0 + t + 1: 1.0, M0 + t: -1.0, U1 + t: -w * R_MINE, U2 + t: -w * R_MINE2}
        k = done_by("orbital", t, e, -w * R_MULE)
        for item in ITEMS:
            e[x(item.name, t)] = e.get(x(item.name, t), 0.0) + item.minerals
        add(e, k, 0, 0)
        e = {G0 + t + 1: 1.0, G0 + t: -1.0, C0 + t: -w * R_GAS}
        for item in ITEMS:
            if item.gas:
                e[x(item.name, t)] = item.gas
        add(e, 0.0, 0, 0)
        # Pay from the stock at the start plus a share of the bucket's income
        e = {M0 + t: 1.0, U1 + t: spend_share * w * R_MINE, U2 + t: spend_share * w * R_MINE2}
        k = done_by("orbital", t, e, spend_share * w * R_MULE)
        for item in ITEMS:
            e[x(item.name, t)] = e.get(x(item.name, t), 0.0) - item.minerals
        add(e, k, 0, np.inf)
        e = {G0 + t: 1.0, C0 + t: spend_share * w * R_GAS}
        for item in ITEMS:
            if item.gas:
                e[x(item.name, t)] = -item.gas
        add(e, 0.0, 0, np.inf)
        # Workers: miners + collectors + builders' share of the bucket <= SCVs done
        e = {U1 + t: 1.0, U2 + t: 1.0, C0 + t: 1.0}
        for s in BUILT_BY_SCV:
            builder_seconds(s, t, e, 1.0 / w)
            if builder_consumed:
                started_by(s, t, e)
        k = alive_by("scv", t, e, -1.0)
        add(e, k, -np.inf, 0)
        if not walks and builder_during_build and w <= min(SECONDS[s] for s in BUILT_BY_SCV) + 1e-9:
            e = {U1 + t: 1.0, U2 + t: 1.0, C0 + t: 1.0}
            for s in BUILT_BY_SCV:
                in_progress(s, t, e)
            k = done_by("scv", t, e, -1.0)
            add(e, k, -np.inf, 0)
        e = {U1 + t: 1.0}; k = done_by("cc", t, e, -2 * FIELDS); add(e, k, -np.inf, 0)
        e = {U2 + t: 1.0}; k = done_by("cc", t, e, -FIELDS); add(e, k, -np.inf, 0)
        e = {C0 + t: 1.0}; k = done_by("refinery", t, e, -3.0); add(e, k, -np.inf, 0)
        e = {}; k = started_by("refinery", t, e); k += done_by("cc", t, e, -2.0); add(e, k, -np.inf, 0)
        # Supply
        e = {}; k = 0.0
        for item in ITEMS:
            if item.supply:
                # Supply is taken at the start and freed at death: started, less those done and dead.
                k += started_by(item.name, t, e, item.supply)
                if item.name in attrition:
                    k += done_by(item.name, t, e, -item.supply)
                    k += alive_by(item.name, t, e, item.supply)
        add(dict(e), k, -np.inf, 200)
        k += done_by("depot", t, e, -8.0)
        k += done_by("cc", t, e, -15.0)
        add(e, k, -np.inf, 0)
        # Producers, in producer-seconds of the bucket
        capacity(t, [("scv", 1.0), ("orbital", 1.0)], lambda e, s: done_by("cc", t, e, s))
        e = {}; k = started_by("orbital", t, e); k += done_by("cc", t, e, -1.0); add(e, k, -np.inf, 0)
        e = {}; k = started_by("cc", t, e); add(e, k, -np.inf, MAX_BASES - 1)
        e = {}; k = started_by("techlab", t, e); k += started_by("reactor", t, e)
        k += done_by("barracks", t, e, -1.0); add(e, k, -np.inf, 0)
        def rax_cap(e, s, t=t):
            k = started_by("techlab", t, e, -s) + started_by("reactor", t, e, -s)
            return k + done_by("barracks", t, e, s) + done_by("techlab", t, e, s) + done_by("reactor", t, e, 2 * s)
        capacity(t, [("marine", 1.0), ("marauder", 1.0)], rax_cap)
        capacity(t, [("marauder", 1.0)], lambda e, s: done_by("techlab", t, e, s))
        capacity(t, [("stim", 1.0)], lambda e, s: done_by("techlab", t, e, s))
        capacity(t, [("medivac", 1.0)], lambda e, s: done_by("starport", t, e, s))
        # Prerequisites
        e = {x("barracks", t): 1.0}; k = done_by("depot", t, e, -BIG); add(e, k, -np.inf, 0)
        e = {x("orbital", t): 1.0}; k = done_by("barracks", t, e, -BIG); add(e, k, -np.inf, 0)
        e = {x("factory", t): 1.0}; k = done_by("barracks", t, e, -BIG); add(e, k, -np.inf, 0)
        e = {x("starport", t): 1.0}; k = done_by("factory", t, e, -BIG); add(e, k, -np.inf, 0)
    e = {}; k = started_by("stim", T - 1, e); add(e, k, -np.inf, 1)

    # A concave piecewise-linear reward on a count is the minimum of its segments' lines: the reward variable sits
    # under each line, and maximizing it takes it to the lowest one.
    for a, segments in curves.items():
        for t in range(1, T + 1):
            value, count = 0.0, 0.0
            for slope, upto in segments:
                e = {r(a, t - 1): 1.0}
                k = alive_by(a, t, e, -slope)
                add(e, k - (value - slope * count), -np.inf, 0)
                value += slope * (upto - count)
                count = upto
    # Coverage of enemy type a over bucket t: at most the enemy's count then (a bound), and at most what the own
    # units done by then are worth against it.
    for a in enemy_types:
        for t in range(1, T + 1):
            e = {cov_var(a, t - 1): 1.0}
            k = 0.0
            for own, against in effectiveness.items():
                if against.get(a):
                    k += alive_by(own, t, e, -against[a])
            add(e, k, -np.inf, 0)
    c = np.zeros(n_vars)
    e = {}
    if terminal:
        for name, wgt in weights.items():
            if name not in curves:
                alive_by(name, T, e, -wgt)
        for a in curve_types:
            c[r(a, T - 1)] -= 1.0
        for a in enemy_types:
            c[cov_var(a, T - 1)] -= enemy_value[a]
    else:
        for t in range(1, T + 1):
            for name, wgt in weights.items():
                if name not in curves:
                    alive_by(name, t, e, -wgt * disc[t - 1])
            for a in curve_types:
                c[r(a, t - 1)] -= disc[t - 1]
            for a in enemy_types:
                c[cov_var(a, t - 1)] -= enemy_value[a] * disc[t - 1]
    for v, coef in e.items():
        c[v] += coef

    lower = np.zeros(n_vars); upper = np.full(n_vars, np.inf)
    lower[M0] = upper[M0] = 50.0
    lower[G0] = upper[G0] = 0.0
    for s, (there, _) in walks.items():
        for t in range(T):
            if edges[t] < there - 1e-9:
                upper[x(s, t)] = 0.0
    for a in enemy_types:
        for t in range(1, T + 1):
            upper[cov_var(a, t - 1)] = max(0.0, float(enemy[a](edges[t])))
    integrality = np.zeros(n_vars); integrality[X0:M0] = 1
    Amat = coo_matrix((vals, (rows, cols)), shape=(len(lo), n_vars)).tocsr()
    return dict(c=c, A=Amat, lo=np.array(lo), hi=np.array(hi), bounds=Bounds(lower, upper),
                integrality=integrality, T=T, edges=edges, x=x)


def solve(model, *, time_limit: float, mip_rel_gap: float = 1e-4):
    t0 = time.perf_counter()
    res = milp(model["c"], constraints=LinearConstraint(model["A"], model["lo"], model["hi"]),
               bounds=model["bounds"], integrality=model["integrality"],
               options={"time_limit": time_limit, "mip_rel_gap": mip_rel_gap, "disp": False})
    return res, time.perf_counter() - t0


def describe(model, res):
    T, x, edges = model["T"], model["x"], model["edges"]
    counts, order = {}, []
    for item in ITEMS:
        total = 0
        for t in range(T):
            n = int(round(res.x[x(item.name, t)]))
            if n:
                order.append((edges[t], item.name, n))
                total += n
        counts[item.name] = total
    order.sort()
    return counts, order


def report(label: str, model, res, seconds: float, *, until: float | None = None) -> None:
    n_int = int(model["integrality"].sum())
    if res.x is None:
        print(f"{label:>32} ints={n_int:4d} {seconds:6.2f}s  NO SOLUTION status={res.status}")
        return
    counts, order = describe(model, res)
    shown = {k: v for k, v in counts.items() if v}
    print(f"{label:>32} ints={n_int:4d} {seconds:6.2f}s objective={-res.fun:9.1f} gap={res.mip_gap:7.1e} status={res.status}  {shown}")
    if until is not None:
        early = [(s, name, n) for s, name, n in order if s < until]
        print(" " * 8, ", ".join(f"{s:.0f}s {name}x{n}" if n > 1 else f"{s:.0f}s {name}" for s, name, n in early))
