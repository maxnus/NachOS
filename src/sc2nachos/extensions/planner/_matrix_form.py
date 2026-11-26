"""A mixed-integer linear program as the solvers take it, and what a solver found for it."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import final

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csr_array

from sc2nachos.extensions.planner._solver import Solver

type FloatArray = NDArray[np.float64]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class MatrixForm:
    """A mixed-integer linear program to maximize, as matrices."""

    objective_coefficients: FloatArray
    """The objective's coefficient on each variable."""
    objective_constant: float
    """The objective's constant term, which no variable multiplies."""
    matrix: csr_array
    """Each row's coefficients, a column to each variable."""
    row_lower: FloatArray
    """Each row's lower bound, `-inf` for none."""
    row_upper: FloatArray
    """Each row's upper bound, `inf` for none."""
    lower: FloatArray
    """Each variable's lower bound, `-inf` for none."""
    upper: FloatArray
    """Each variable's upper bound, `inf` for none."""
    integral: FloatArray
    """1 for each integral variable and 0 for each other."""


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
    """Whether it proved the values the best, to the relative gap it was asked for."""
    seconds: float
    """The wall-clock seconds it took."""
    message: str
    """What it said of the solve."""
    solver: Solver
    """The solver that found it."""

    @classmethod
    def none_found(cls, *, seconds: float, message: str, solver: Solver) -> Solution:
        """A solve by `solver` that found no values in `seconds`, and ended with `message`."""
        return cls(None, math.nan, math.inf, False, seconds, message, solver)
