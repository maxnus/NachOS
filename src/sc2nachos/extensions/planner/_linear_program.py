"""A mixed-integer linear program built a run of rows at a time, solved by HiGHS or Gurobi."""

from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import assert_never, final

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import coo_array, csr_array

from sc2nachos.extensions.planner._gurobi import maximize_with_gurobi
from sc2nachos.extensions.planner._highs import maximize_with_highs
from sc2nachos.extensions.planner._matrix_form import FloatArray, MatrixForm, Solution
from sc2nachos.extensions.planner._solver import Solver


@final
@dataclass(frozen=True, slots=True)
class VariableBlock:
    """A run of a program's variables, from `start`, `size` long."""

    start: int
    """The index of its first variable."""
    size: int
    """How many variables it holds."""

    def to_slice(self) -> slice:
        """Where its variables are among the program's, as a slice of them."""
        return slice(self.start, self.start + self.size)


@final
class RowExpression:
    """A linear expression in each of a run of rows: a matrix of coefficients on each block of variables it involves,
    one row of it to each row, and a constant for each row."""

    __slots__ = ("_terms", "constant", "rows")

    def __init__(self, rows: int) -> None:
        self.rows = rows
        self._terms: dict[VariableBlock, FloatArray] = {}
        self.constant: FloatArray = np.zeros(rows)

    @property
    def coefficients(self) -> Mapping[VariableBlock, FloatArray]:
        """The matrix of coefficients on each block of variables it involves, a row to each of its rows."""
        return MappingProxyType(self._terms)

    def add(self, block: VariableBlock, coefficients: FloatArray | float) -> RowExpression:
        """Add `coefficients`, a matrix of a row to each of this expression's rows and a column to each of `block`'s
        variables, or one number for every one of them; returns this expression."""
        matrix = np.broadcast_to(np.asarray(coefficients, dtype=float), (self.rows, block.size))
        if block in self._terms:
            self._terms[block] = self._terms[block] + matrix
        else:
            self._terms[block] = matrix.copy()
        return self

    def add_expression(self, other: RowExpression, factor: FloatArray | float = 1.0) -> RowExpression:
        """Add `other`, a row of it to each row of this one, each row times `factor`, one number or one a row; returns
        this expression."""
        scale = np.broadcast_to(np.asarray(factor, dtype=float), (self.rows,))
        for block, matrix in other.coefficients.items():
            self.add(block, matrix * scale[:, None])
        self.constant = self.constant + other.constant * scale
        return self

    def selected(self, rows: NDArray[np.bool_]) -> RowExpression:
        """This expression in the rows `rows` marks alone."""
        chosen = RowExpression(int(rows.sum()))
        for block, matrix in self._terms.items():
            chosen.add(block, matrix[rows])
        chosen.constant = self.constant[rows]
        return chosen

    def summed(self, weights: FloatArray) -> RowExpression:
        """One row: this expression's rows, each times its weight in `weights`, summed."""
        total = RowExpression(1)
        for block, matrix in self._terms.items():
            total.add(block, weights @ matrix)
        total.constant = np.array([weights @ self.constant])
        return total

    def value_of(self, values: FloatArray) -> FloatArray:
        """Each row's value with the variables at `values`."""
        result = self.constant.copy()
        for block, matrix in self._terms.items():
            result += matrix @ values[block.to_slice()]
        return result

    def nonzero_coefficients(self, first_row: int) -> tuple[list[FloatArray], list[FloatArray], list[FloatArray]]:
        """Each coefficient but 0 as scipy's sparse matrices take them, in coordinate format: its row index, its
        variable index and its value, the rows counted from `first_row`."""
        rows: list[FloatArray] = []
        columns: list[FloatArray] = []
        coefficients: list[FloatArray] = []
        for block, matrix in self._terms.items():
            row_indices, column_indices = np.nonzero(matrix)
            rows.append((row_indices + first_row).astype(float))
            columns.append((column_indices + block.start).astype(float))
            coefficients.append(matrix[row_indices, column_indices])
        return rows, columns, coefficients


@final
class LinearProgram:
    """A mixed-integer linear program: blocks of variables, each bounded and integral or not, and runs of rows, each
    a linear expression held between bounds."""

    __slots__ = ("_columns", "_coefficients", "_integral", "_lower", "_row_lower", "_row_upper", "_rows", "_upper")

    def __init__(self) -> None:
        self._lower: list[FloatArray] = []
        self._upper: list[FloatArray] = []
        self._integral: list[FloatArray] = []
        self._rows: list[FloatArray] = []
        self._columns: list[FloatArray] = []
        self._coefficients: list[FloatArray] = []
        self._row_lower: list[FloatArray] = []
        self._row_upper: list[FloatArray] = []

    @property
    def variable_count(self) -> int:
        """How many variables it holds."""
        return sum(len(lower) for lower in self._lower)

    @property
    def row_count(self) -> int:
        """How many rows it holds."""
        return sum(len(lower) for lower in self._row_lower)

    @property
    def integral_count(self) -> int:
        """How many of its variables are integral."""
        return int(sum(integral.sum() for integral in self._integral))

    def add_variables(
        self,
        size: int,
        *,
        lower: FloatArray | float = 0.0,
        upper: FloatArray | float = math.inf,
        integral: NDArray[np.bool_] | bool = False,
    ) -> VariableBlock:
        """A new block of `size` variables, each between `lower` and `upper` and integral where `integral` says, each
        one value or one a variable."""
        block = VariableBlock(self.variable_count, size)
        self._lower.append(np.broadcast_to(np.asarray(lower, dtype=float), (size,)).copy())
        self._upper.append(np.broadcast_to(np.asarray(upper, dtype=float), (size,)).copy())
        self._integral.append(np.broadcast_to(np.asarray(integral, dtype=float), (size,)).copy())
        return block

    def constrain(
        self, expression: RowExpression, *, lower: FloatArray | float = -math.inf, upper: FloatArray | float = math.inf
    ) -> None:
        """Hold each row of `expression` between `lower` and `upper`, one value or one a row."""
        rows, columns, coefficients = expression.nonzero_coefficients(self.row_count)
        self._rows += rows
        self._columns += columns
        self._coefficients += coefficients
        self._row_lower.append(
            np.broadcast_to(np.asarray(lower, dtype=float), (expression.rows,)) - expression.constant
        )
        self._row_upper.append(
            np.broadcast_to(np.asarray(upper, dtype=float), (expression.rows,)) - expression.constant
        )

    def maximize(
        self,
        objective: RowExpression,
        *,
        solver: Solver,
        time_limit: float | None,
        node_limit: int | None,
        relative_gap: float,
    ) -> Solution:
        """Solve for the greatest `objective`, a single row, with `solver`, within `time_limit` seconds of wall-clock
        time and `node_limit` nodes of branching, each unlimited if `None`. The solver stops once it has proven its
        values within `relative_gap` of the best, relative to the objective. A program Gurobi refuses, as one larger
        than its licence allows, HiGHS solves within what is left of `time_limit`."""
        form = self._matrix_form(objective)
        started = time.perf_counter()
        match solver:
            case Solver.GUROBI:
                solution = maximize_with_gurobi(
                    form, time_limit=time_limit, node_limit=node_limit, relative_gap=relative_gap
                )
            case Solver.HIGHS:
                # HiGHS solves it below, as it does a program Gurobi refuses.
                solution = None
            case _:
                assert_never(solver)
        if solution is not None:
            return solution
        remaining = None if time_limit is None else time_limit - (time.perf_counter() - started)
        if remaining is not None and remaining <= 0:
            message = "the time limit was reached before HiGHS could start"
            return Solution.none_found(seconds=time.perf_counter() - started, message=message, solver=Solver.HIGHS)
        solution = maximize_with_highs(form, time_limit=remaining, node_limit=node_limit, relative_gap=relative_gap)
        return dataclasses.replace(solution, seconds=time.perf_counter() - started)

    def _matrix_form(self, objective: RowExpression) -> MatrixForm:
        """The program as matrices, to maximize `objective`, a single row."""
        size = self.variable_count
        coefficients = np.zeros(size)
        _, columns, values = objective.nonzero_coefficients(0)
        if columns:
            np.add.at(coefficients, np.concatenate(columns).astype(int), np.concatenate(values))
        matrix, row_lower, row_upper = self._constraint_matrix_and_bounds(size)
        return MatrixForm(
            objective_coefficients=coefficients,
            objective_constant=float(objective.constant[0]),
            matrix=matrix,
            row_lower=row_lower,
            row_upper=row_upper,
            lower=np.concatenate(self._lower),
            upper=np.concatenate(self._upper),
            integral=np.concatenate(self._integral),
        )

    def _constraint_matrix_and_bounds(self, size: int) -> tuple[csr_array, FloatArray, FloatArray]:
        """Every row: the matrix, and each row's lower and upper bound."""
        if not self._rows:
            return csr_array((0, size)), np.zeros(0), np.zeros(0)
        matrix = coo_array(
            (
                np.concatenate(self._coefficients),
                (np.concatenate(self._rows).astype(int), np.concatenate(self._columns).astype(int)),
            ),
            shape=(self.row_count, size),
        )
        return matrix.tocsr(), np.concatenate(self._row_lower), np.concatenate(self._row_upper)
