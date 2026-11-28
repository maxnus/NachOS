# The build planner

`sc2nachos.extensions.planner.Planner`, for Terran, works out what to make and when. A bot hands it a `Reward`, which
says what units and upgrades are worth to it, and reads back a `BuildPlan`: the things to make, in order, each with the
time it is expected to start. Whether to expand or to make marines now follows from the economy the planner models,
not from a rule. The planner gives no orders and holds no unit: where a structure goes, which SCV builds it and what to
drop once the game moves on stay the bot's.

This page says how to use it. What each argument and each field is, with its default, is in the docstrings, which the
[API reference](https://maxnus.github.io/NachOS/sc2nachos/extensions/planner.html) shows.

## A first plan

The planner is an [extension](extensions.md), built in the bot's own object:

```python
from sc2nachos.extensions.planner import Planner, Reward


class Extensions:
    def __init__(self, api: Api) -> None:
        self.planner = Planner(api)


@api.on(TurnEvent)
def replan(event: TurnEvent) -> None:
    if api.step % 448 == 0:
        reward = Reward.weighted({UnitTypeId.MARINE: 1.0, UnitTypeId.MEDIVAC: 2.0})
        plan = api.extensions.planner.plan_now(reward)
```

`plan_now` solves before it returns, within 3 seconds by default, so a turn that plans is that much longer. It returns
`None` if the solver finds no plan within its limits, and `planner.plan` holds the last plan found.

## The reward

A reward says what each unit is worth for every second of the plan it is alive, so a unit made sooner is worth more.
Later seconds count less, by the reward's `half_life`.

- **A number** makes every unit of a type worth that much: a marine 1 a second and a medivac 2, above.
- **A `WeightCurve`** in its place makes more units worth less each, so that a plan buys a mix rather than only the
  best type for its cost. `WeightCurve.up_to(1.0, 8)` is a count of 8 the bot wants, and nothing past it.
- **A `RewardTerm`** is for anything more, and a reward takes any number as its `terms`: several unit types counted
  together, a worth between two game times or one that changes with the time, and a worth that needs an upgrade first.
  `RewardTerm.combination` is for units worth something only together, as medivacs and the infantry they heal.
- **`upgrade_weights`** are what each upgrade done is worth.

A unit counts as one type whatever form it is in: a sieged tank as a siege tank, and a hellbat as a hellion.

A plan makes only what the reward values and what that needs, with SCVs, supply depots, command centers, orbital
commands, refineries and add-ons. It makes nothing that pays back only after its `horizon`, 5 minutes by default: a
reward on army units alone then takes no expansion, and one that also values SCVs or command centers takes one it is
handed. Over 10 minutes a plan weighs an expansion itself, and a solve takes half again to twice as long. Under 4
minutes a plan's first steps get worse.

## Carrying a plan out

- **In order, not to the second.** `plan.items` are in the order the planner expects them to start, and a bot starts
  each as soon as it can pay for it. The times are the model's, which starts everything at the beginning of an interval
  of its grid.
- **The near term only.** Far ahead the planner plans in fractions, which is far faster to solve. An item from there
  reads `is_fluid`: it is what the plan expects to follow, not what it settled on. A bot carries out a plan's first
  steps and plans again.
- **Workers.** `plan.intervals[0].collectors` is how many workers the plan has collecting vespene now. A bot keeps that
  many at its refineries and has every other worker mine.
- **What the plan expects.** Each interval's `start` and `end` hold the stock, what was collected, and the units and
  upgrades expected then, so `plan.intervals[-1].end.units` is what the plan comes to.
- **In a log.** `plan.to_string()` is the whole plan as text, a line an item, and `item.to_string()` one such line,
  `"3:12 SUPPLY_DEPOT"`.

[`examples/terran_planner_bot.py`](../examples/terran_planner_bot.py) is a whole bot that carries plans out so, for a
reward on SCVs, marines and marauders, more in a mix, with Stimpack and as the game goes on, and five upgrades: a
`RewardTerm.combination`, a term with `requirements` and a `time_factor`. It plans every 20 seconds and starts the
items due in the plan's order, each as soon as a unit can make it, setting aside the cost of one it cannot pay for yet
so that nothing after it spends that money first. It keeps as many SCVs at its refineries as the plan has collecting
now.

## What a plan rests on

Everything the planner assumes of the game is the bot's to read and change, as arguments of `plan_now`:

| Argument | What it is |
|---|---|
| `expansions` | The expansions the bot means to take next, in order. A plan starts no more command centers than these, so none by default. |
| `state` | The state of the game the plan starts from: `planner.state()` by default, plain values a bot may change first. |
| `economy` | An `EconomyModel`: what workers and MULEs bring in, how long an SCV walks to build, and how long after a new base is done its fields can be mined. |
| `loss_rates` | The share of each unit type the bot expects to lose a second, a number or a [value that depends on the game time](time-dependent-values.md). Only what is expected to survive counts. |
| `start_limits` | The most a plan may start of a type, for what the bot will not make. |
| `time_limit`, `node_limit`, `relative_gap` | When the solve may stop. A node limit gives the same plan on every machine, for tests and recorded games. |

A base about to fall left out, the MULEs' energy spent on scans, no reactor on a starport and the natural to take:

```python
planner = api.extensions.planner
state = planner.state()
state = dataclasses.replace(state, mineral_fields=state.mineral_fields - len(falling_base.mineral_fields.alive))
plan = planner.plan_now(
    reward,
    state=state,
    economy=EconomyModel(mule_minerals_per_orbital_second=0.0),
    start_limits={UnitTypeId.REACTOR_STARPORT: 0},
    expansions=[natural],
)
```

`Planner(api, ...)` takes how a plan is laid out in time: the grid of intervals, for how long each item is planned in
whole numbers, and how the model rounds a start and a build to the grid. The defaults suit a normal game.

## Solving with Gurobi

Gurobi, a commercial solver, finds the same plans faster than HiGHS, which comes with SciPy:

```python
# pip install sc2nachos[gurobi]
from sc2nachos.extensions.planner import Planner, Solver

self.planner = Planner(api, solver=Solver.GUROBI)
```

Gurobi runs on one thread, on what HiGHS's presolve leaves of a plan's program, about half its rows.

- **The licence gurobipy comes with** is free, size-limited, for non-production, non-commercial use only, as
  [Gurobi says](https://support.gurobi.com/hc/en-us/articles/29682074018833), and expires with its release, gurobipy
  14's on 2028-11-29. Its limit, 2000 variables and 2000 rows after the presolve, fits a plan on the default grid over 5
  minutes, and may not fit a finer grid, a longer horizon or a reward on many types. A licence bought from Gurobi, or an
  academic one, has no limit.
- **A plan Gurobi refuses** is found by HiGHS within what is left of the time limit, with a warning in the log, and
  `plan.solve_report.solver` says which solver found it. HiGHS prints its banner once, the first time it presolves.

## How far a plan was off

`plan.deviation_of(planner.state())`, some seconds after the plan was made, is the game's numbers less what the plan
expected by then, so the plan was optimistic where one is negative. Read together, they say what to change:

- **Less `collected`:** the workers brought in less than the `EconomyModel` has them bring, so lower its rates.
- **As much collected, more `resources` in the bank:** the bot started less than the plan had it start.
- **As much of both, fewer `units`:** units were lost, which `loss_rates` are for.

The stock and the units are best compared soon after a plan is made, where its intervals are short. What was collected
compares at any time.
