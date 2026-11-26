"""Solving a program with Gurobi, on what HiGHS's presolve leaves of it, HiGHS's postsolve mapping Gurobi's values
back. Imported only to solve with `Solver.GUROBI`, since gurobipy and highspy are an optional install."""

from __future__ import annotations

import dataclasses
import time
from types import MappingProxyType

import gurobipy as gp
import highspy
import numpy as np
from gurobipy import GRB
from loguru import logger
from scipy.sparse import csc_array, csr_array

from sc2nachos.extensions.planner._matrix_form import FloatArray, MatrixForm, Solution, no_solution
from sc2nachos.extensions.planner._solver import Solver

# Each of Gurobi's status codes, by name, for what a solve says.
_GUROBI_STATUS_NAMES = MappingProxyType({getattr(GRB.Status, name): name for name in dir(GRB.Status) if name.isupper()})
# What HiGHS's presolve leaves a program to solve from. Its enums are compared by value, not with `is`: highspy hands
# back a new object for each.
_PRESOLVED = (
    highspy.HighsPresolveStatus.kReduced,
    highspy.HighsPresolveStatus.kNotReduced,
    highspy.HighsPresolveStatus.kReducedToEmpty,
)


def start_gurobi() -> None:
    """Start Gurobi and stop it again. Raises `RuntimeError` if it cannot start, as without a licence."""
    try:
        gp.Env(params={"OutputFlag": 0}).dispose()
    except gp.GurobiError as error:
        raise RuntimeError(f"Gurobi cannot start: {error}") from error


def maximize_with_gurobi(
    form: MatrixForm, *, time_limit: float | None, node_limit: int | None, relative_gap: float
) -> Solution | None:
    """The best values Gurobi finds for `form` on one thread, within `time_limit` seconds of wall-clock time, HiGHS's
    presolve and postsolve counted in it, and `node_limit` nodes of branching, each unlimited if `None`. It stops once
    it has proven its values within `relative_gap` of the best, relative to the objective. Gurobi solves what HiGHS's
    presolve leaves of `form`, and HiGHS's postsolve maps its values back.

    `None`, logged as a warning, where Gurobi refuses the program left, as one larger than its licence allows, or HiGHS
    cannot map Gurobi's values back.
    """
    started = time.perf_counter()
    highs = _presolving_highs(form, time_limit=time_limit)
    presolve_status = highs.getModelPresolveStatus()
    if presolve_status not in _PRESOLVED:
        message = f"HiGHS's presolve ended {presolve_status.name}"
        return no_solution(seconds=time.perf_counter() - started, message=message, solver=Solver.GUROBI)
    remaining = None if time_limit is None else time_limit - (time.perf_counter() - started)
    if remaining is not None and remaining <= 0:
        message = "the time limit was reached in HiGHS's presolve"
        return no_solution(seconds=time.perf_counter() - started, message=message, solver=Solver.GUROBI)
    presolved = highs.getPresolvedLp()
    matrix = _matrix_of(presolved)
    try:
        found = _minimize_presolved(
            presolved, matrix, time_limit=remaining, node_limit=node_limit, relative_gap=relative_gap
        )
    except gp.GurobiError as error:
        logger.warning("Gurobi refused the program, so HiGHS solves it: {}", error)
        return None
    if found.values is None:
        return dataclasses.replace(found, seconds=time.perf_counter() - started)
    values = _postsolved(highs, found.values, matrix)
    if values is None:
        logger.warning("HiGHS could not map Gurobi's values back to the program, so HiGHS solves it")
        return None
    return dataclasses.replace(
        found,
        values=values,
        objective=float(form.objective @ values) + form.objective_constant,
        seconds=time.perf_counter() - started,
    )


def _presolving_highs(form: MatrixForm, *, time_limit: float | None) -> highspy.Highs:
    """HiGHS, quiet, handed `form` to minimize its objective's negative, and presolved within `time_limit` seconds."""
    highs = highspy.Highs()
    highs.setOptionValue("output_flag", False)
    if time_limit is not None:
        highs.setOptionValue("time_limit", float(time_limit))
    row_count = len(form.row_lower)
    variable_count = len(form.objective)
    program = highspy.HighsLp()
    program.num_row_ = row_count
    program.num_col_ = variable_count
    program.col_cost_ = -form.objective
    program.col_lower_ = form.lower
    program.col_upper_ = form.upper
    program.row_lower_ = form.row_lower
    program.row_upper_ = form.row_upper
    program.a_matrix_.format_ = highspy.MatrixFormat.kRowwise
    program.a_matrix_.num_row_ = row_count
    program.a_matrix_.num_col_ = variable_count
    program.a_matrix_.start_ = form.matrix.indptr.astype(np.int32)
    program.a_matrix_.index_ = form.matrix.indices.astype(np.int32)
    program.a_matrix_.value_ = form.matrix.data
    program.integrality_ = [
        highspy.HighsVarType.kInteger if integral else highspy.HighsVarType.kContinuous for integral in form.integral
    ]
    highs.passModel(program)
    highs.presolve()
    return highs


def _matrix_of(program: highspy.HighsLp) -> csr_array:
    """`program`'s rows' coefficients, a column to each variable."""
    shape = (program.num_row_, program.num_col_)
    if program.num_row_ == 0:
        # A presolve that removes every row leaves the matrix it was handed in place.
        return csr_array(shape)
    stored = program.a_matrix_
    parts = (np.asarray(stored.value_), np.asarray(stored.index_), np.asarray(stored.start_))
    if stored.format_ == highspy.MatrixFormat.kColwise:
        return csr_array(csc_array(parts, shape=shape))
    return csr_array(parts, shape=shape)


def _minimize_presolved(
    program: highspy.HighsLp,
    matrix: csr_array,
    *,
    time_limit: float | None,
    node_limit: int | None,
    relative_gap: float,
) -> Solution:
    """The least objective Gurobi finds for `program`, whose rows' coefficients are `matrix`, on one thread within its
    limits: the values of `program`'s variables, and the objective with its offset. A program of no variables, which
    presolve solved whole, needs no Gurobi. Raises `gurobipy.GurobiError` where Gurobi refuses it."""
    if program.num_col_ == 0:
        message = "HiGHS's presolve left nothing to solve"
        return Solution(np.zeros(0), float(program.offset_), 0.0, True, 0.0, message, Solver.GUROBI)
    parameters: dict[str, float | int] = {"OutputFlag": 0, "Threads": 1, "MIPGap": relative_gap}
    if time_limit is not None:
        parameters["TimeLimit"] = time_limit
    if node_limit is not None:
        parameters["NodeLimit"] = node_limit
    integral = np.array([kind == highspy.HighsVarType.kInteger for kind in program.integrality_], dtype=bool)
    with gp.Env(params=parameters) as environment, gp.Model(env=environment) as model:
        variables = model.addMVar(
            program.num_col_,
            lb=np.asarray(program.col_lower_),
            ub=np.asarray(program.col_upper_),
            vtype=np.where(integral, GRB.INTEGER, GRB.CONTINUOUS) if integral.size else GRB.CONTINUOUS,
        )
        model.setObjective(np.asarray(program.col_cost_) @ variables + program.offset_, GRB.MINIMIZE)
        _add_rows(model, variables, matrix, lower=np.asarray(program.row_lower_), upper=np.asarray(program.row_upper_))
        model.optimize()
        message = _GUROBI_STATUS_NAMES.get(model.Status, f"status {model.Status}")
        if model.SolCount == 0:
            return no_solution(seconds=model.Runtime, message=message, solver=Solver.GUROBI)
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


def _postsolved(highs: highspy.Highs, values: FloatArray, matrix: csr_array) -> FloatArray | None:
    """The values of the program `highs` presolved, mapped back from `values` of the program its presolve left, whose
    rows' coefficients are `matrix`; `None` where its postsolve cannot."""
    solution = highspy.HighsSolution()
    solution.col_value = values
    solution.row_value = matrix @ values
    solution.value_valid = True
    if highs.postsolve(solution) == highspy.HighsStatus.kError:
        return None
    postsolved = highs.getSolution()
    if not postsolved.value_valid:
        return None
    return np.asarray(postsolved.col_value, dtype=float)
