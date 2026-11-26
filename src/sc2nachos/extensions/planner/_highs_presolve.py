"""HiGHS's presolve of a program, and its postsolve back. highspy is imported only where it is used, since it is an
optional install."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, final

import numpy as np
from scipy.sparse import csc_array, csr_array

from sc2nachos.extensions.planner._matrix_form import FloatArray, MatrixForm

if TYPE_CHECKING:
    import highspy


class PresolveOutcome(Enum):
    """How HiGHS's presolve of a program ended."""

    REDUCED = "reduced"
    """It left a program to solve, smaller or as it was."""
    SOLVED = "solved"
    """It fixed every variable, leaving nothing to solve."""
    INFEASIBLE = "infeasible"
    """It found that no values satisfy the program, or that the program is unbounded."""
    TIMED_OUT = "timed out"
    """It reached the time limit."""
    FAILED = "failed"
    """It failed otherwise."""


@final
class HighsPresolve:
    """HiGHS's presolve of a program, done within `time_limit` seconds, `None` for no limit: the program it leaves, and
    the postsolve that maps values of that program back to values of the program handed in."""

    __slots__ = ("_highs", "_outcome", "_presolved")

    def __init__(self, form: MatrixForm, *, time_limit: float | None) -> None:
        import highspy

        self._highs = highspy.Highs()
        self._highs.setOptionValue("output_flag", False)
        if time_limit is not None:
            self._highs.setOptionValue("time_limit", float(time_limit))
        self._highs.passModel(_highs_program_of(form))
        self._highs.presolve()
        self._outcome = _outcome_of(self._highs.getModelPresolveStatus())
        left_to_solve = self._outcome in (PresolveOutcome.REDUCED, PresolveOutcome.SOLVED)
        self._presolved = _form_of(self._highs.getPresolvedLp()) if left_to_solve else None

    @property
    def outcome(self) -> PresolveOutcome:
        """How the presolve ended."""
        return self._outcome

    @property
    def presolved(self) -> MatrixForm | None:
        """The program the presolve left, of no variables where it fixed them all, its objective that of the program
        handed in less that program's constant; `None` where the presolve left no program, as `outcome` says why."""
        return self._presolved

    def postsolved(self, values: FloatArray) -> FloatArray | None:
        """The values of the program handed in that `values` of `presolved` map back to, `None` where the postsolve
        cannot map them."""
        import highspy

        if self._presolved is None:
            raise ValueError(f"the presolve left no program to postsolve: it ended {self._outcome.value}")
        solution = highspy.HighsSolution()
        solution.col_value = values
        solution.row_value = self._presolved.matrix @ values
        solution.value_valid = True
        if self._highs.postsolve(solution) == highspy.HighsStatus.kError:
            return None
        postsolved = self._highs.getSolution()
        if not postsolved.value_valid:
            return None
        return np.asarray(postsolved.col_value, dtype=float)


def _highs_program_of(form: MatrixForm) -> highspy.HighsLp:
    """`form` as HiGHS takes a program, to minimize its objective's negative without its constant."""
    import highspy

    row_count = len(form.row_lower)
    variable_count = len(form.objective_coefficients)
    program = highspy.HighsLp()
    program.num_row_ = row_count
    program.num_col_ = variable_count
    program.col_cost_ = -form.objective_coefficients
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
    return program


def _outcome_of(status: highspy.HighsPresolveStatus) -> PresolveOutcome:
    """The outcome HiGHS's presolve status says. Its enums are compared by value, not with `is`: highspy hands back a
    new object for each."""
    import highspy

    outcomes = {
        highspy.HighsPresolveStatus.kReduced: PresolveOutcome.REDUCED,
        highspy.HighsPresolveStatus.kNotReduced: PresolveOutcome.REDUCED,
        highspy.HighsPresolveStatus.kReducedToEmpty: PresolveOutcome.SOLVED,
        highspy.HighsPresolveStatus.kInfeasible: PresolveOutcome.INFEASIBLE,
        highspy.HighsPresolveStatus.kUnboundedOrInfeasible: PresolveOutcome.INFEASIBLE,
        highspy.HighsPresolveStatus.kTimeout: PresolveOutcome.TIMED_OUT,
    }
    return next((outcome for kind, outcome in outcomes.items() if kind == status), PresolveOutcome.FAILED)


def _form_of(program: highspy.HighsLp) -> MatrixForm:
    """`program`, which minimizes, as a `MatrixForm` to maximize the negative of its objective."""
    import highspy

    integral = [kind == highspy.HighsVarType.kInteger for kind in program.integrality_]
    return MatrixForm(
        objective_coefficients=-np.asarray(program.col_cost_, dtype=float),
        objective_constant=-float(program.offset_),
        matrix=_matrix_of(program),
        row_lower=np.asarray(program.row_lower_, dtype=float),
        row_upper=np.asarray(program.row_upper_, dtype=float),
        lower=np.asarray(program.col_lower_, dtype=float),
        upper=np.asarray(program.col_upper_, dtype=float),
        integral=np.asarray(integral, dtype=float) if integral else np.zeros(program.num_col_),
    )


def _matrix_of(program: highspy.HighsLp) -> csr_array:
    """`program`'s rows' coefficients, a column to each variable."""
    import highspy

    shape = (program.num_row_, program.num_col_)
    if program.num_row_ == 0:
        # A presolve that removes every row leaves the matrix it was handed in place.
        return csr_array(shape)
    stored = program.a_matrix_
    parts = (np.asarray(stored.value_), np.asarray(stored.index_), np.asarray(stored.start_))
    if stored.format_ == highspy.MatrixFormat.kColwise:
        return csr_array(csc_array(parts, shape=shape))
    return csr_array(parts, shape=shape)
