"""A mixed-integer linear program built a run of rows at a time, solved by HiGHS through scipy."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, final

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import milp
from scipy.sparse import coo_array, csr_array

# Where the solver stops: within this of the best plan, relative to its worth.
RELATIVE_GAP: Final = 1e-4
_OPTIMAL_STATUS: Final = 0

type FloatArray = NDArray[np.float64]


@final
@dataclass(frozen=True, slots=True)
class VariableBlock:
    """A run of a program's variables, from `start`, `size` long."""

    start: int
    """The index of its first variable."""
    size: int
    """How many variables it holds."""


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
            result += matrix @ values[block.start : block.start + block.size]
        return result

    def triplets(self, first_row: int) -> tuple[list[FloatArray], list[FloatArray], list[FloatArray]]:
        """The row index, the variable index and the coefficient of each coefficient but 0, its rows counted from
        `first_row`."""
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
@dataclass(frozen=True, slots=True)
class Solution:
    """What the solver found."""

    values: FloatArray | None
    """Each variable's value, or `None` if it found none within its limits."""
    objective: float
    """The objective's value, `nan` without values."""
    gap: float
    """Its bound on how far the objective may be from the best, relative to it."""
    is_optimal: bool
    """Whether it proved the values the best, to `RELATIVE_GAP`."""
    seconds: float
    """The wall-clock seconds it took."""
    message: str
    """What it said of the solve."""


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
        rows, columns, coefficients = expression.triplets(self.row_count)
        self._rows += rows
        self._columns += columns
        self._coefficients += coefficients
        self._row_lower.append(
            np.broadcast_to(np.asarray(lower, dtype=float), (expression.rows,)) - expression.constant
        )
        self._row_upper.append(
            np.broadcast_to(np.asarray(upper, dtype=float), (expression.rows,)) - expression.constant
        )

    def maximize(self, objective: RowExpression, *, time_limit: float | None, node_limit: int | None) -> Solution:
        """Solve for the greatest `objective`, a single row, within `time_limit` seconds of wall-clock time and
        `node_limit` nodes of branching, each unlimited if `None`."""
        size = self.variable_count
        costs = np.zeros(size)
        _, columns, coefficients = objective.triplets(0)
        if columns:
            np.add.at(costs, np.concatenate(columns).astype(int), -np.concatenate(coefficients))
        options: dict[str, float | int | bool] = {"disp": False, "mip_rel_gap": RELATIVE_GAP}
        if time_limit is not None:
            options["time_limit"] = time_limit
        if node_limit is not None:
            options["node_limit"] = node_limit
        started = time.perf_counter()
        result = milp(
            costs,
            constraints=self._constraint_matrix_and_bounds(size),
            bounds=(np.concatenate(self._lower), np.concatenate(self._upper)),
            integrality=np.concatenate(self._integral),
            options=options,
        )
        seconds = time.perf_counter() - started
        if result.x is None:
            return Solution(None, math.nan, math.inf, False, seconds, str(result.message))
        gap = getattr(result, "mip_gap", None)
        return Solution(
            values=np.asarray(result.x, dtype=float),
            objective=-float(result.fun) + float(objective.constant[0]),
            gap=0.0 if gap is None else float(gap),
            is_optimal=result.status == _OPTIMAL_STATUS,
            seconds=seconds,
            message=str(result.message),
        )

    def _constraint_matrix_and_bounds(self, size: int) -> tuple[csr_array, FloatArray, FloatArray]:
        """Every row, as scipy takes them: the matrix, and each row's lower and upper bound."""
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
