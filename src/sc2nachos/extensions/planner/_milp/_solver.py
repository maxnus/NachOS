"""The solvers the build planner finds a plan with."""

from enum import Enum


class Solver(Enum):
    """The solver the build planner finds a plan with."""

    HIGHS = "highs"
    """HiGHS, through SciPy."""
    GUROBI = "gurobi"
    """Gurobi, on one thread, which `pip install sc2nachos[gurobi]` installs with HiGHS beside it. Gurobi solves what
    HiGHS's presolve leaves of the program, and HiGHS's postsolve maps Gurobi's values back. With the size-limited
    licence gurobipy comes with, the program left must have at most 2000 variables and 2000 rows, a row held between
    two bounds taking a variable of its own; a program Gurobi refuses, as one larger than that, is solved by HiGHS
    within what is left of the time limit, and the plan's `SolveReport.solver` is then `HIGHS`."""
