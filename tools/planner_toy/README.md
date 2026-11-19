# The planner's toy model

The toy Terran build-order MILP behind the measurements in [the planner's plan](../../.claude/plans/planner-plan.md):
14 item types, an arbitrary time grid, a discounted reward with concave segments and enemy coverage, per-race builder
occupancy with walks, and loss rates. `toy_milp.py` builds and solves it with scipy's HiGHS; each `run*.py` is one of
the experiments, run from this folder, for example:

    uv run python run7.py double45 90 10

The runs take a time limit in seconds and print the solve time, the objective, the gap, the counts and the first part
of the plan. They were the seed of the planner, `sc2nachos.extensions.planner`, and are kept as they were measured,
outside ruff and pyright: `tools/benchmark_planner.py` runs their cases on the planner itself, from the game's data.
