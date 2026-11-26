"""Solving a program with Gurobi, on what HiGHS's presolve leaves of it. gurobipy and highspy are imported only where
they are used, since they are an optional install."""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

from sc2nachos.extensions.planner._highs_presolve import HighsPresolve
from sc2nachos.extensions.planner._matrix_form import FloatArray, MatrixForm, Solution
from sc2nachos.extensions.planner._solver import Solver

if TYPE_CHECKING:
    import gurobipy as gp
    from scipy.sparse import csr_array


def check_gurobi_starts() -> None:
    """Raise `ImportError` if gurobipy or highspy is not installed, and `RuntimeError` if Gurobi cannot start, as
    without a licence."""
    try:
        import gurobipy as gp
        import highspy  # noqa: F401
    except ImportError as error:
        message = "Solver.GUROBI needs gurobipy and highspy, which `pip install sc2nachos[gurobi]` installs"
        raise ImportError(message) from error
    try:
        gp.Env(params={"OutputFlag": 0}).dispose()
    except gp.GurobiError as error:
        raise RuntimeError(f"Gurobi cannot start: {error}") from error


def maximize_with_gurobi(
    form: MatrixForm, *, time_limit: float | None, node_limit: int | None, relative_gap: float
) -> Solution | None:
    """The best values Gurobi finds for `form` on one thread, within `time_limit` seconds of wall-clock time, HiGHS's
    presolve and postsolve counted in it, and `node_limit` nodes of branching, each unlimited if `None`. It stops once
    it has proven its values within `relative_gap` of the best, relative to the objective less its constant. Gurobi
    solves what HiGHS's presolve leaves of `form`, and HiGHS's postsolve maps its values back.

    `None`, logged as a warning, where Gurobi refuses the program left, as one larger than its licence allows, or HiGHS
    cannot map Gurobi's values back.
    """
    import gurobipy as gp

    started = time.perf_counter()
    presolve = HighsPresolve(form, time_limit=time_limit)
    presolved = presolve.presolved
    if presolved is None:
        message = f"HiGHS's presolve ended {presolve.outcome.value}"
        return Solution.none_found(seconds=time.perf_counter() - started, message=message, solver=Solver.GUROBI)
    remaining = None if time_limit is None else time_limit - (time.perf_counter() - started)
    if remaining is not None and remaining <= 0:
        message = "the time limit was reached in HiGHS's presolve"
        return Solution.none_found(seconds=time.perf_counter() - started, message=message, solver=Solver.GUROBI)
    try:
        found = _maximize(presolved, time_limit=remaining, node_limit=node_limit, relative_gap=relative_gap)
    except gp.GurobiError as error:
        logger.warning("Gurobi refused the program, so HiGHS solves it: {}", error)
        return None
    if found.values is None:
        return dataclasses.replace(found, seconds=time.perf_counter() - started)
    values = presolve.postsolved(found.values)
    if values is None:
        logger.warning("HiGHS could not map Gurobi's values back to the program, so HiGHS solves it")
        return None
    return dataclasses.replace(
        found,
        values=values,
        objective=float(form.objective_coefficients @ values) + form.objective_constant,
        seconds=time.perf_counter() - started,
    )


def _maximize(form: MatrixForm, *, time_limit: float | None, node_limit: int | None, relative_gap: float) -> Solution:
    """The greatest objective Gurobi finds for `form` on one thread within its limits. A program of no variables needs
    no Gurobi. Raises `gurobipy.GurobiError` where Gurobi refuses it."""
    import gurobipy as gp
    from gurobipy import GRB

    variable_count = len(form.objective_coefficients)
    if variable_count == 0:
        return Solution(np.zeros(0), form.objective_constant, 0.0, True, 0.0, "nothing to solve", Solver.GUROBI)
    parameters: dict[str, float | int] = {"OutputFlag": 0, "Threads": 1, "MIPGap": relative_gap}
    if time_limit is not None:
        parameters["TimeLimit"] = time_limit
    if node_limit is not None:
        parameters["NodeLimit"] = node_limit
    with gp.Env(params=parameters) as environment, gp.Model(env=environment) as model:
        variables = model.addMVar(
            variable_count,
            lb=form.lower,
            ub=form.upper,
            vtype=np.where(form.integral > 0, GRB.INTEGER, GRB.CONTINUOUS),
        )
        model.setObjective(form.objective_coefficients @ variables + form.objective_constant, GRB.MAXIMIZE)
        _add_rows(model, variables, form.matrix, lower=form.row_lower, upper=form.row_upper)
        model.optimize()
        message = _status_name_of(model.Status)
        if model.SolCount == 0:
            return Solution.none_found(seconds=model.Runtime, message=message, solver=Solver.GUROBI)
        return Solution(
            values=np.asarray(variables.X, dtype=float),
            objective=float(model.ObjVal),
            gap=float(model.MIPGap) if model.IsMIP else 0.0,
            is_optimal=model.Status == GRB.OPTIMAL,
            seconds=model.Runtime,
            message=message,
            solver=Solver.GUROBI,
        )


def _add_rows(model: gp.Model, variables: gp.MVar, matrix: csr_array, *, lower: FloatArray, upper: FloatArray) -> None:
    """Hold each row of `matrix` times `variables` between its bound in `lower` and in `upper`. A row held between two
    bounds becomes an equality to a variable of its own between them, as Gurobi's `addRange` makes it."""
    bounded_below = np.isfinite(lower)
    bounded_above = np.isfinite(upper)
    equal = bounded_below & bounded_above & (lower == upper)
    ranged = bounded_below & bounded_above & ~equal
    if equal.any():
        model.addConstr(matrix[equal] @ variables == upper[equal])
    if (at_most := ~bounded_below & bounded_above).any():
        model.addConstr(matrix[at_most] @ variables <= upper[at_most])
    if (at_least := bounded_below & ~bounded_above).any():
        model.addConstr(matrix[at_least] @ variables >= lower[at_least])
    if ranged.any():
        ranges = model.addMVar(int(ranged.sum()), lb=lower[ranged], ub=upper[ranged])
        model.addConstr(matrix[ranged] @ variables - ranges == 0)


def _status_name_of(status: int) -> str:
    """The name of Gurobi's status code `status`, as `OPTIMAL` or `TIME_LIMIT`."""
    from gurobipy import GRB

    names = (name for name in dir(GRB.Status) if name.isupper() and getattr(GRB.Status, name) == status)
    return next(names, f"status {status}")
