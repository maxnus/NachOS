"""Solving a program with HiGHS, through SciPy."""

from __future__ import annotations

import time

import numpy as np
from scipy.optimize import milp

from sc2nachos.extensions.planner._matrix_form import MatrixForm, Solution
from sc2nachos.extensions.planner._solver import Solver


def maximize_with_highs(
    form: MatrixForm, *, time_limit: float | None, node_limit: int | None, relative_gap: float
) -> Solution:
    """The best values HiGHS finds for `form` within `time_limit` seconds of wall-clock time and `node_limit` nodes of
    branching, each unlimited if `None`. It stops once it has proven its values within `relative_gap` of the best,
    relative to the objective."""
    options: dict[str, float | int | bool] = {"disp": False, "mip_rel_gap": relative_gap}
    if time_limit is not None:
        options["time_limit"] = time_limit
    if node_limit is not None:
        options["node_limit"] = node_limit
    started = time.perf_counter()
    result = milp(
        -form.objective_coefficients,
        constraints=(form.matrix, form.row_lower, form.row_upper),
        bounds=(form.lower, form.upper),
        integrality=form.integral,
        options=options,
    )
    seconds = time.perf_counter() - started
    if result.x is None:
        return Solution.none_found(seconds=seconds, message=str(result.message), solver=Solver.HIGHS)
    gap = getattr(result, "mip_gap", None)
    return Solution(
        values=np.asarray(result.x, dtype=float),
        objective=-float(result.fun) + form.objective_constant,
        gap=0.0 if gap is None else float(gap),
        # Scipy's status 0: the solver proved its values the best, to the gap asked for.
        is_optimal=result.status == 0,
        seconds=seconds,
        message=str(result.message),
        solver=Solver.HIGHS,
    )
