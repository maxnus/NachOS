# Build planner: plan

A planner that, given a reward, a horizon and the state of the game, returns what to make and when: an ordered list of
items, each with the time the model expects it to start. The bot executes the list in order, each item as soon as it
is affordable, and asks for a new list on a cadence. The reward is the input: a bot that wants a large marine army,
a certain number of workers or a counter to what it has scouted says so in the reward, and whether to expand first
or build marines now follows from the economics in the model, not from a rule.

The planner is a NachOS extension, `sc2nachos.extensions.planner`, in the NachOS repository (the owner,
2026-11-07), built by a bot's `Extensions` class as speed mining is and read as `api.extensions.planner`. It reads
the game through NachOS's public api and nothing else, as every extension must. Placement, which worker builds what,
micro and the enemy's behaviour stay the bot's: the enemy enters the planner as constants the bot supplies from its
intel, and the bot's construction manager carries out the plan's structures. AvocaDOS is its first consumer and the
bot its design is tested on, and nothing in it may exist for AvocaDOS alone.

This page holds the decisions, with the measurements behind them, the design, the phases and what is still open.
It lives in NachOS as `.claude/plans/planner-plan.md`, beside `next-steps.md`, which points to it as the work in
progress; AvocaDOS's `docs/plans/` keeps a pointer here, since phases 2 and 4 are AvocaDOS's. The measurements are
from a toy Terran model of 14 item types solved by scipy's HiGHS on a 32-core machine. The toy and this page were
drafted in AvocaDOS and moved with phase 1's pull request, the toy as `tools/planner_toy/` in its first commit,
f1da1b6, and the toy was taken out again before the merge (the owner, 2026-11-23, asked "Should we really commit the
toy to the repo btw?", and then: "Remove it"). It was prototype code on made-up numbers, the one folder left out of
ruff and pyright, with the count-form cut of Open 8 in its fluid intervals, so its counts and objectives are
somewhat off; `tools/benchmark_planner.py` runs its cases on the planner. What only the toy measured, the first ten
rows of the table below and a few sentences of the decisions, can be run again from that commit alone.

## Decisions

1. **A time-indexed MILP, solved by HiGHS through scipy.** Starts of each item per interval are integer or fluid
   variables; minerals, vespene and supply are linear flows; producers, workers and prerequisites are linear
   constraints. Scipy is already a dependency and HiGHS is among the best open-source solvers; a solver of our own
   would not beat it, and the Python side of a solve costs under 0.2 s. The lever is the formulation, below. No
   compiled code of our own: the ladder zip takes compiled packages from locked manylinux wheels, and an extension
   of ours would need a wheel built in CI.
2. **A grid that widens with time, cut to a worker's build time: intervals of 34 game steps doubling from 544** (the
   owner, 2026-11-19, over 2-second intervals doubling every 45 seconds, 63 in all), 68 until 1,088, 136 until 2,176,
   272 until 4,352 and 544 after, 1.5 seconds to 24.3, 57 intervals by ten minutes. Ten-second intervals undertrained
   workers, an SCV every 20 to 30 seconds instead of every 12, and undervalued the plan by 5%; 2 seconds was within
   0.01% of 1 second at half the solve time, but still trained an SCV every 14 seconds for the game's 12.14, a start
   sitting on an interval's first second (Open 9). Every build time is a whole number of game steps, and of 16: a
   worker's 272, of every race, are eight intervals of 34, and stay a whole number of intervals while the intervals
   double to 272, so SCVs chain as in the game; 2 seconds are 44.8 steps and divide nothing. A doubling grid keeps the
   relative timing error constant along the plan. Durations are never rounded: a build enters the producer constraints
   in exact seconds split across the intervals it overlaps; only a start sits on an interval's first second and a
   completion on the next edge. A bot hands `Planner` any other `TimeGrid`. `TimeGrid.doubling` takes
   `initial_interval_length`, `initial_interval_count`, an even number, and `max_interval_doublings` (the owner,
   2026-11-19, the names too), counts where it took the time of the first doubling and the longest interval: a time need
   not be a whole number of intervals, and the grid then doubled at the last edge before it, at 0:44 for the 0:45 it was
   asked, which a grid cut to a worker cannot afford.
3. **Whole numbers where an item is lumpy, fluid flows where it is not**, for longer the more it costs (the owner,
   phase 1) and the less comes in (the owner, 2026-11-19): an item's starts are integer from the plan's start for 60
   seconds for each 100 minerals and vespene at the income a game starts with, at least 30, and fluid after: a
   command center to 4:00, a factory or a starport to 2:30, a barracks or an orbital command to 1:30, a depot to
   1:00, an SCV or a marine to 0:30. At a higher income the time is shorter in proportion, never under the 30
   seconds, and at a lower one no longer: the income is what the state's SCVs mine if every one mines, with its
   orbital commands' MULEs, against a game's twelve SCVs on eight fields, each by the economy the plan is handed. An
   upgrade has its time by its cost as a unit has (the owner, 2026-11-19), where phase 1 kept it integer over any
   horizon. A bot sets its own for any unit type or upgrade, `Planner(fluid_after=...)` taking a function of either
   that returns `None` for the default, named so by the owner (2026-11-19) over `whole_until` and
   `whole_starts_until`. A plan's item says whether it is fluid, `PlanItem.is_fluid` (the owner, 2026-11-19), since
   a fluid one stands for fractions summed and rounded. The rule: an item stays whole where its cost exceeds an
   interval's income and it is made once an interval at most. The owner's question behind the income was whether a type
   the state holds many of could be fluid sooner, 60 workers against 12: a time divided by the count of the type
   gained little alone and nothing beside the income's, which already brings most items to the 30 seconds, and was
   not built (Open 3 holds the measurements).
   Fluid far-term starts make the far term the value function of the near term, computed by the LP at each node
   instead of learned; the near-term plan did not change when the window moved from 1:30 to 10:00. Depots kept whole
   for minutes stall the solver: 22 near-identical integer starts are symmetric branches. Phase 1 first kept
   townhalls, production and tech structures whole over the whole horizon and the rest for 90 seconds, which left the
   reference configuration 7.6 to 12% from its bound after 3 seconds and 7 to 11% after 10; by cost it was proven
   optimal in 8 to 10 seconds, and is in 2.5 to 4.3 since each item's starts are held as running totals (Open 7).
4. **The reward is a discounted sum over time of concave terms in one currency.** Never the terminal state alone:
   under replanning a terminal reward defers the army forever, and it is far harder to solve. The discount
   half-life, not the horizon, is the economy knob: workers appear once it exceeds a worker's payback time, an
   expansion once it reaches a few minutes. Linear weights buy only the best type per mineral; concave
   piecewise-linear terms, the integral of a utility curve, buy a mix. Allowed: linear terms, concave piecewise
   terms, minima of linear things (complementarity, coverage), shortfall penalties, linear timing constraints. Not
   allowed without binaries: convex bonuses, thresholds, products of counts, capped penalties.
5. **The enemy enters as per-interval constants.** Coverage, each enemy type worth covering up to its count by what
   the own army is worth against it from the unit profiles' effectiveness matrix, is a minimum and so concave; it
   flipped the toy from 50 marauders against armored to 168 marines against air, and ramped marines ahead of air
   forecast for 4:00. Scenarios combine by expectation or by worst case, both concave. A bad matchup is a linear
   penalty or a loss rate, never a capped one.
6. **Expected losses are survival factors on completions**, per type and interval, from recent deaths, the matchup and
   planned engagements, at no cost in variables. Only survivors count for income, supply, reward and coverage. They
   turn the plan from reaching an army into sustaining one: economy and production capacity first, continuous
   production after, lost workers replaced.
7. **The builder is an occupancy interval per race, and walks shift it.** Terran's SCV is away from the walk there
   through the build to the walk back; Protoss's probe for the two walks only; Zerg's drone walks there and is gone.
   Walk times are per structure, and the bot's (decision 11): `EconomyModel.builder_walk_time`, by default 10 seconds to
   a command center, about the walk from a main's mineral line to its natural on every map of the pool (corpus), and 6
   to any other structure; the planner reads no path for them. A structure cannot start before its walk unless a worker
   is already on its way. Walks cost Terran 2% and two SCVs.
8. **A plan is executed as an order, not a timetable.** The next item starts when affordable; the predicted times
   are for monitoring and the debug overlay. In AvocaDOS the order maps onto the priorities of the production,
   construction and research objectives, so placement, workers and cancellation stay where they are.
9. **Replanning is parameterised**: an interval in game time, zero for continuous, a wall-time budget per solve, and
   event triggers (a structure lost, the forecast changed, an item unplaceable, an attack beginning). The solve runs in
   a background process; the bot applies the plan on arrival, dropping what it already started. A new plan is never
   worse only if the old plan is a candidate, by a warm start from the shifted previous plan or by re-scoring it. A
   synchronous mode with a node limit is deterministic for tests and recordings. A hysteresis bonus for the previous
   plan's near term was part of this, required beside the old plan as a candidate, and was built as `plan_now`'s
   `previous_plan` and `hysteresis_bonus`. It is taken out (the owner, 2026-11-23: "do we actually really need the
   hysteresis bonus? Does it cost performance? Does it make the code more complicated?", and then: "Yes, remove it"): no
   game has shown it needed, an argument added later breaks no bot where one taken out does, and it comes back in phase
   3 if its games show plans flipping, from the commit before the one that took it out, bfff4b6. What was measured of it
   is in Open 16.
10. **No caps on production structures.** Their number is bounded by the economics; the physical bounds stay: what
    the bot lets a plan start of an item, `start_limits`, its expansions say, with no limit by default, two
    refineries a base, one add-on a barracks, one of each upgrade, supply 200, and room to build near a base once the
    map supplies it.
11. **Every input is visible and the bot's to change** (the owner, review of #239). What the game shows is a public
    `PlannerState`, which `Planner.state` reads and `plan_now(state=...)` takes back changed, a base about to fall left
    out, say; it holds only what the game shows, never an assumption. What the bot assumes is an `EconomyModel` handed
    to each `plan_now`: what each worker at a field or a refinery brings in a second, a list from the first worker on,
    the MULEs' income and the walks; and `loss_rates`, the `expansions` to take next and `start_limits` beside it,
    the most a plan may start of a unit type or an upgrade, none for 0 (the owner, 2026-11-19, over
    `new_command_center_limit`, now one entry of it): a plan makes only what the reward values and what that needs, so
    the limits are for what the model adds itself, add-ons, the orbital command and expansions, and a bot that opts out
    by situation hands another mapping to that plan. The constructor keeps only how to solve: the grid, the limits and
    the windows; the limits have since moved to `plan_now` (Open 18). Workers stay pooled over every field and refinery
    (Open 10). The plan a new one keeps to and the bonus for it were `plan_now`'s `previous_plan` and `hysteresis_bonus`
    too (the owner, 2026-11-18, over the reward holding them, and over the planner keeping to its own last plan, an
    input the bot would not see): they say how plans follow one another, not what the bot wants, so a reward is built
    once and handed to every plan. Both are taken out (decision 9), and go there again if they come back.

## Measurements

| what | result |
|---|---|
| 7 min, 10 s intervals, 588 integers, integrated reward | usable plan in 1 s, proven within 5% in 4 s, 4% gap left at 60 s |
| same, terminal reward | 10 to 30 s for a sensible plan |
| 10 min, uniform 10 s, 840 integers | 24% gap after 10 s |
| 10 min, widening grid, integer to 1:30, 126 integers | proven optimal in 1.2 s |
| 10 min, 2 s near term, integer to 1:30, 630 integers | optimal in 4.2 s; 1 s intervals, 1260 integers, in 7.9 s |
| 10 min, doubling grid, 476 integers | optimal in 5.8 s |
| big structures whole to 10:00, +112 integers | 0.5% gap at 10 s, optimal at 25 s, same near-term plan |
| concave reward | a mix of types; 9% gap at 10 s instead of 0.5% |
| all-fluid LP | 10 ms, 3% above optimal, fractions of a command center over three intervals, no rounding feasible |
| Python matrix build | 18 to 150 ms |
| the planner's reference, starts held as running totals | optimal in 2.5 to 4.3 s, from 8.4 to 10.4 |
| replans from 3:00 and 6:00, times by the state's income, 245 to 265 integers from 419 | optimal in 2.1 to 5.2 s, from 4.2 to 18.2 |
| a wide reward, 38 items, upgrades' times by cost, 1,291 integers from 1,494 | optimal in 14 to 30 s, from 36 to 38 and a 2.1% gap at 40 s |
| the planner's reference on intervals of 34 steps doubling from 544, 57 intervals, 401 integers | optimal in 1.5 to 2.2 s, from 2.5 to 4.3 |
| replans and the wide reward on that grid | optimal in 1.8 to 3.6 s, from 2.1 to 5.2, and in 5.1 to 13.1 s, from 7 to 30 |

The gap is the solver's bound on how far the incumbent may be from optimal, relative to the whole objective; a
large one means a poor incumbent or a loose bound, and the plans were judged by their content. Objectives are not
comparable across grids, since each grid rounds differently; the simulator of phase 6 is the judge of a plan in
absolute terms.

## Design

A package `src/sc2nachos/extensions/planner/`, laid out as speed mining is: `__init__.py` exports `Planner` and the
reward's data types, and the rest is private modules. NachOS depends on numpy and scipy already, so HiGHS comes with
it. Everything reads the game through the public api, which `tests/test_extensions.py` holds every extension to, so
the package could move to one of its own unchanged.

```python
class Extensions:
    def __init__(self, api: Api[object]) -> None:
        self.speed_mining = SpeedMining(api)
        self.planner = Planner(api, grid=Grid.doubling(GameTime(seconds=2)), replan=Replan(interval=...))
```

- `_model.py`: the items the race can make, read from `api.data`: cost, build time, supply used and provided,
  producers and their capacity, tech requirements, morphs and add-ons; the race's rules on top: what occupies a
  townhall (a Terran morph, a Zerg queen), the builder's interval, MULE income, chrono boost and warp-in later. The
  item set is pruned to what the reward names, their prerequisites, producers, supply, workers, expansions and
  refineries. Each unit type counts as one of its `equivalent_types` (#248): the one an order makes rather than one
  morphed from another, and of two, the one needing fewer techs, so a hellbat counts as a hellion (the owner: one unit
  to a planner); a rich refinery counts as the refinery its order makes.
- `_grid.py`: interval edges from segments. Each item's integrality window comes from its cost, or from the bot.
- `_planner_state.py`: the state as plain values, `PlannerState`, read from the api when `plan_now` asks: resources,
  supply, the alive count of each type, each item in production with its time left, each structure an SCV builds and
  whether one walks there first, upgrades done and researching, and the fields, refineries and free geysers at the bot's
  bases. A bot may change it and hand it back.
- `_economy_model.py`: `EconomyModel`, what the bot assumes of its economy (decision 11), with defaults measured in
  game.
- `_reward.py`: the reward as data the matrix builder reads, and the extension's public interface: per-type weights or
  concave segments, coverage (effectiveness, enemy counts per interval, values), floors, the discount half-life and the
  terminal value. The bot builds it; the planner never guesses it.
- `_milp.py`: the matrix from model, grid, snapshot and reward; the solve with a time or node limit; the plan from
  the solution.
- `_build_plan.py`: the `BuildPlan`: items in order with predicted start times and the predicted resource and count
  curves, read as `planner.plan`.
- `_runner.py`: `plan_now()` synchronous, and the background mode: one process per `Planner`, since two apis in one
  process must coexist and NachOS keeps no module-level state; the interval, the triggers the bot raises, and
  re-anchoring a plan on arrival.

The planner orders nothing. It never touches a unit, and it holds nothing a `UnitDict` should, since a game state
holds plain values. It handles no event: it reads the game when `plan_now` asks.

In AvocaDOS, `Extensions` in `core/api.py` builds the planner, and a `PlanManager` extending `BotManager` builds
the reward from the strategy, raises the triggers, keeps the current plan, sets the priorities of the next items'
objectives, and draws the plan in the debug overlay. A build order becomes a reward with constraints: `cc_first` is a
command center before any barracks and a worker-heavy discount, beside the hand-written objectives it keeps for
bunkers and scouting. Until a NachOS release carries the planner, AvocaDOS plays it from the checkout with
`uv run --with-editable ~/git/NachOS python scripts/run.py`, as its `AGENTS.md` says.

## Working in NachOS

NachOS's `.claude/CLAUDE.md` and `AGENTS.md` apply to every phase there: one step at a time, a design change planned
with the owner before it is built, a branch off a fetched `origin/main`, a pushed commit frozen, measurements and the
run that proved them in the commit message and the PR body, and every pull request reviewed on GitHub before it is
called ready. A decision recorded in `.claude/plans/next-steps.md` stands until the owner reverses it, and the planner
goes in there as the work in progress. Its scope line, which lists build orders and economy management as not
NachOS's, is amended in the first PR, as "Open" records: a planner is a solver any bot hands its reward, and the
strategy stays the bot's. The quality bar is NachOS's from the first line, met with fewer, larger PRs per phase.

## Phases

1. **The extension, for Terran, synchronous**, in NachOS, built in phase 1's pull request, its exit's 3 seconds met
   since the grid was cut to a worker's build time (Open 7). Model from `api.data`, grid, reward, matrix, solve,
   plan, `plan_now()`. Tests on tiny instances with known optimal plans (the first depot at 8 seconds from 50
   minerals and twelve SCVs; one marine at a time from one barracks; the command center with the fifth SCV after
   uninterrupted SCVs), on the toy's cases as `tools/benchmark_planner.py`, and on made-up games through
   `sc2nachos.testing` for the snapshot. A section under "NachOS's own" in `docs/extensions.md`, this plan moved
   in, and `next-steps.md` pointing here. Exit: the toy's results reproduced from the game's data, and the
   reference configuration, doubling grid with the windows above, solved within 3 seconds on a developer machine.
2. **Playing it**, in AvocaDOS against the checkout. `PlanManager` driving objective priorities, the overlay, and a
   build `BuildOrderName.PLANNER` in `enabled_builds` with a static reward. Games against the built-in AI through
   `scripts/run.py`. Exit: it wins against VeryHard on the pool's maps as the current builds do, and plays a
   recorded game to the end without an error logged. Then a NachOS release and the pin bump, and `/sc2-match`.
3. **Replanning**, in NachOS. The background process, the interval and continuous mode, the triggers, a hysteresis bonus
   again if its games show plans flipping (decision 9), and the old plan kept as a candidate by re-scoring: its starts
   delayed until the new state can afford them, by the planner's own program through scipy, and the better of it and the
   new solve's plan kept. No highspy to start with (the owner, 2026-11-23, Open 2), which was to bring a warm start as
   an optional dependency, its wheel in AvocaDOS's ladder zip (`LOCKED_WHEELS` in `scripts/release_helper.py`). Exit:
   AvocaDOS's step time unchanged with planning on, and a release on the ladder.
4. **The reward from intel**, in AvocaDOS. Coverage from the unit profiles and the enemy's counts, a forecast from
   scouted tech, loss rates from the memory's deaths and the matchup, floors from the threat. The composition
   optimizer's target as a progress term, or its objective as segments. Exit: the composition responds in scripted
   scenarios, and the ladder results do not fall.
5. **The other races**, in NachOS. Protoss: power and pylons, chrono boost, warp-in. Zerg: creep, larva as a flow
   per hatchery, injects, the consumed drone. Exit: the demo bot, or a made-up game of each race, plans without an
   error, and the docs say what each race's model holds.
6. **Later, if wanted.** An event-driven simulator that replays a plan or a recorded game's orders through the same
   income model, to validate the model against recordings, re-time a plan to the second, and serve a sequence-search
   solver for rewards the MILP cannot express. A compiled simulator is the one place compiled code would pay, and
   would be a package of its own.

## Open

Each point is as it stands. How each was reached, with every measurement, is in the commit messages of #239, and in this
file as it was at `807304c`.

1. **NachOS's scope line: settled** (the owner, 2026-11-07). The planner is in NachOS's own extensions, and
   `AGENTS.md`'s scope line says so: a solver any bot hands its objective is in scope, as pathfinding is a solver over
   the map, while the objective itself, strategy, build orders and economy rules, stays the bot's. A package of its own,
   `sc2nachos-planner`, was weighed and set aside: the model's coupling to the game data is tested in-tree, and the
   public-api test keeps a later split a day's work.
2. **highspy: not to start with** (the owner, 2026-11-23, who had it measured early, "should we really wait that long?",
   and to the recommendation: "Ok agreed"). Phase 3 starts on scipy alone: the old plan's starts delayed until the new
   state can afford them, scored by the planner's own program, and the better of it and the new solve's plan kept.
   Measured with highspy 1.16.0 beside scipy's HiGHS 1.12.0 on seven cases. The direct call is no faster, 29.4 s in all
   against 29.8. A start from the problem's own best plan still takes most of a solve to prove. A start helps a wide
   reward under a 1-second limit, and only from a plan the new state can afford, since HiGHS drops one it cannot. Where
   the old plan had to be delayed first (the owner: "Ok go on"), the warm start gave nothing that scoring it again does
   not. highspy is looked at again only if games show a need, and then for a solve stopped from outside and plans handed
   over as they are found, which scipy has not.
3. **The item set: a wide reward is not proven in 3 seconds, and is left so** (the owner, 2026-11-23: "Leave it as is").
   A reward on 13 army types and 8 upgrades brings 38 items and up to 1,251 whole variables, where the reference has 14
   and about 400. On today's planner over 5 minutes (`tools/benchmark_planner.py items`, twelve cases) it has a plan
   after 3 s in all twelve, 0.1 to 1.5% short of the best where not proven, and is proven in 2.4 to 3.7 s from a game's
   start and 7 to 22 s from mid-game. The time limit is the bot's, `SolveReport.gap` says how far a plan may be from the
   best, a reward on fewer types is proven sooner, and most of what a plan 1% short lacks is in the far term. Not built:
   pruning to what a plan can reach within its horizon, which phase 2's games may call for. An upgrade done or being
   researched stays an item, since leaving those out changed nothing that shows. Where no plan is found in time,
   `plan_now` returns `None` and `plan` stays the one before (the owner, 2026-11-19), where it raised.
4. **Build space.** A bound on structures per base from the map, as the one physical limit the model lacks;
   `api.placement` may answer it.
5. **Replanning defaults.** The interval and the budget are parameters; their defaults come out of phase 3's games, as
   does whether a hysteresis bonus is needed (decision 9).
6. **The discount against the solver.** A discount made a 7-minute solve faster and a 10-minute one slower; settle it on
   the real model.
7. **Phase 1's exit, the reference configuration solved within 3 seconds, is met**: proven optimal in 1.5 to 2.2 s over
   10 minutes since 2026-11-19, and in 1.4 to 2.5 s over the default 5. It took 8 to 10 s at first. Two changes got it
   there: each item's starts counted so far by each interval's end (2026-11-18, the owner: in #239, not a pull request
   of its own), the program's 87,024 coefficients becoming 14,417, and the grid cut to a worker's build time (decision
   2).
8. **The count-form cut holds only where an interval's starts are whole: confirmed** (the owner, 2026-11-23: "Yes
   confirm"). The toy applied it in fluid intervals too, where it is no valid cut: in 16-second intervals a barracks
   made a marine every 32 seconds, not every 17.86. Held to whole intervals, the toy's linear case was worth 6.4% more,
   with 7 barracks instead of 13.
9. **A build no interval divides is rounded to the grid either way.** A start sits on an interval's first second, so
   production chained on one producer starts a whole number of intervals apart. For workers the grid answers it
   (decision 2): an SCV starts every 272 steps, as in the game. For the rest, a build that runs into an interval by less
   than a share of it leaves the interval free: `Planner(max_ignored_overrun=...)`, a quarter by default (the owner,
   2026-11-20, to whom a marauder's 480 steps planned as 544 was plainly wrong, and who chose a parameter and the
   quarter over the nearest interval). A marauder is then planned every 476 steps, a siege tank every 714 for its 720,
   and a marine every 408 for its 400. Not to be built (the owner, 2026-11-19): starts queued behind the build ahead,
   worth some 0.07% on a grid where every chain is exact, for a count done by each item and interval. Interval lengths
   of each item's own were weighed and were no better.
10. **Workers per base: not built** (the owner, 2026-11-23: "I would really like to have workers per base, but I don't
    think we can afford it"). The planner pools workers over every field and gas building, and a new base's fields count
    from when its townhall is done. The one charge for a move is `EconomyModel.initial_harvest_delay`, 3 seconds, with
    no number of its own for a new base (the owner, the same day: "SCVs should already be delayed by 3 seconds when they
    start to mine or change their assignment to the new base"). The error left is estimated at 30 to 75 minerals an
    expansion. Workers per base, with transfers, would roughly double the program's variables, estimated and not
    measured; a prototype outside the package is a few hours' work, and not made.
11. **What an item needs first is whole** (the owner, 2026-11-20, and 2026-11-21, who made it a fraction and named it,
    the planner to offer many arguments since nobody knows yet how it works best). `Planner(unlock_fraction=1.0)`, the
    default, adds a 0 or 1 for each needed tech and interval. Without it a hundredth of a factory unlocked a starport,
    and a bot that followed the plan never built the factory. With 0 the program is what it was. It costs 64 whole
    variables on the reference, proven in 2.3 to 4.4 s from 1.7 to 2.5 over 10 minutes, which the default 5 minutes make
    up for (Open 12). Open: a wide reward from mid-game, which a fraction above 0 leaves unproven in 3 s.
12. **`plan_now` plans over 5 minutes by default** (the owner, 2026-11-21), where every measurement before was over the
    toy's 10. Horizons of 4 to 6 minutes plan the next 30 seconds alike, within 0.7% of the best, and 3 minutes do not,
    up to 5.1% off. What a short horizon cannot see is what pays back late: over 5 minutes a reward on army units alone
    starts no command center, where over 10 it starts one at 0:49. The owner: the expansion is built if the bot adds it
    to the reward. 1 a second for each SCV beside that reward starts it at 0:49 over 5 minutes too.
13. **A plan says how its workers split, and moving one costs time** (the owner, 2026-11-22: "we definitely want to
    report this in the plan and we definitely want to have an interruption when reassigning a worker, before the new
    income kicks in. Something like 2 or 3s would be enough"). What followed from it, all the owner's, 2026-11-22 and
    2026-11-23:
    - **The split and the delay.** A `PlanInterval` holds its `miners` and `collectors`, and
      `EconomyModel.initial_harvest_delay`, 3 seconds by default, is how long a worker that starts to collect brings in
      nothing. The 3 seconds are assumed, not measured in game.
    - **Every start of mining pays too** ("I think workers immediately assigned to mining should pay too"): a new SCV's
      first walk to a field, and a worker moved between minerals and vespene. A builder back from its structure pays its
      walk back instead. The solve is a tenth to a quarter slower in all, and a plan from a game's start worth 0.6 to
      1.5% less.
    - **A plan holds its intervals** ("Would it not be better to have a new class that represents all these values for a
      single bucket and then have a single tuple over objects of this class?"), each a `PlanInterval`. An interval was a
      bucket until he renamed it ("I don't really like the name").
    - **An interval runs from one moment to the next**: a `PlanMoment` holds what is expected at an edge of the grid,
      and one interval's end is the next one's start, the same object. Decided against, for now: an interval holding its
      items, since a bot carries a plan's items out in order.
    - **A plan can be held against the game later** ("I think we need some mechanism to evaluate where a plan was
      optimistic or pessimistic. And I mean not just for development, but even for the user, so they can tweak their
      parameters"): `BuildPlan.deviation_of(state)` returns a `PlanDeviation`, whose `collected`, `resources` and
      `units` say which of the economy, the bot and losses was off. `moment_at` reads what the model has within an
      interval, which jumps at the edges, not a straight line ("Didn't you say before that straight line interpolation
      is wrong for the plan?"). Left for phase 2: when each item started against when it was planned, and whether the
      planner keeps a history.
    - **A reward on units is a list of terms** ("ideally we only have one generic way to specify unit weights and
      provide helpers for simpler cases"). A `RewardTerm` counts groups of units and holds what their count is worth; a
      plain weight, a curve, a floor and a coverage were four spellings of it. It stays one class with helpers. A
      reward's parts stay mappings rather than functions, since the types a reward names are the items a plan weighs.
      Added to a term the same day, none at a cost in solve time that shows: `requirements`, upgrades it is worth
      nothing without, each of which a plan starts whole; `RewardTerm.combination`, the smallest count of several groups
      (asked for synergies "such as between Medivac and bio units"; the name his, and the groups in one list: "if they
      are symmetric, it's generally weird that one comes first and the other second"); `start` and `end` ("just having a
      start and end time on each reward item and then use multiple items to build the time dependence"); `time_factor`
      ("a `Callable[[GameTime], float]` to the RewardTerm, which returns a scaling factor for the entire curve for each
      point in time"); and `count_factor` ("let's add count_factor too").
    - **The stock left at the horizon is worth a little**: `Reward.resource_weight`, 0.0001 a mineral and a vespene by
      default. Income is then never worth nothing, so no worker stands idle in a plan's last intervals and no start of
      mining is counted that no worker makes. Open: the default suits weights around 1, and a reward of much smaller
      ones wants it smaller, which nothing checks.
14. **A start is paid from the stock at its interval's start and half the interval's income** (the owner, 2026-11-23:
    "Is this not too optimistic in the planner, that we can use resources that are yet to come in to already start the
    build time at the start of the interval?"). The half rounds a start to the nearer edge of the grid, and is
    `Planner(spendable_income_fraction=...)` ("Agreed to the Planner argument"), for a bot whose plans prove optimistic
    to lower. Measured against finer grids on the growing reward: in the first 24 seconds a half is at most 0.3%
    optimistic; after them the army at the horizon is within 0.3% from 0:00 and 6:00 and 4.7% too large from 3:00, where
    the long intervals are at fault more than the share. Some seconds of income in place of a share ("Maybe that would
    scale better across different interval lengths, or is the percentage the better approach?") scaled no better.
    `BuildPlan.moment_at` reads the stock below nothing where the model has it so ("Wouldn't it be more truthful however
    to just report the negative stock?", after "let's go with never below zero" the same day).
15. **Three exported names say what they are of** (the owner, 2026-11-23: "I agree with the three renames, I will review
    the other names with the rest of the pr"). `BuildPlan` was `Plan`, `PlannerState` was `GameState` and `EconomyModel`
    was `Economy`, and `Planner.game_state()` is `Planner.state()` ("Maybe it should be called `Planner.state()`?", and
    then: "Rename it"). A bot imports the classes bare and may have a game state or an economy of its own, and
    `test_nachos_names_no_extension` keeps every extension's exported name out of the core's source.
16. **The code review of 2026-11-23 left three points, all settled since** (the owner: "OK do the code review and
    afterwards I will do my review"). Fixed with it: an add-on of a type the plan does not make left its host free; a
    curve's piece with no count to end at was taken; the harvest delay charged no miner where the economy has no rate
    for a collector; `Reward.weighted` took a numpy number for a curve; and a state made by hand with a unit in
    production that is no item raised a `KeyError`. The three:
    - **A supply deficit was forgiven for the whole plan, and is held now** ("Yes, build it"). From 20 supply used of
      15, a plan started one depot and six marines, 26 supply against a cap of 23. A row in deficit now takes a whole
      variable for whether the shortfall is made up, and nothing more is used unless it is. A state with no deficit gets
      the program it had; one with a deficit up to 45 variables more, and a wide reward's proof 2 to 5 s longer.
    - **The hysteresis bonus was paid in shares, and is taken out** ("I don't think we can afford to make the planning
      more expensive just for the hysteresis bonus", and then: "Yes, remove it", decision 9). The third marine expected
      earned a third of the bonus for one started, and exact would have been a whole variable for each item of the
      previous plan. Plans do flip without it; whether that costs a game is phase 3's to find.
    - **Of the loss rates of two types that count as one, the highest is taken** ("The highest"), a hellion's and a
      hellbat's; `start_limits` takes the lowest. A negative rate stays refused ("Would there be anything fundamentally
      wrong with negative rates?"): the program solves with one, and its units multiply for nothing.
17. **A loss rate may change with the game time** (the owner, 2026-11-23: "I would build the time dependent rate for
    sure. I'm not sure about the one-off losses: can these not also be modelled by a sharp peak in the time dependent
    rate?"). A rate stays the share of the units lost a second, which keeps a count linear in the plan's starts. A
    number of units a second would need a whole variable for each unit type and interval, and is left out. A rate is
    read at every game step of the plan rather than once an interval, so that a peak loses its share wherever it falls
    on the grid: a loss at once is a peak, with no argument of its own. A rate is a number or a `TimeDependent[float]`
    (#261, which came of his asking for `TimeSeries` here, 2026-11-24: "Should we not utilise this more, e.g. for the
    time dependent loss rate?"; and "B", over a function read a step at a time): a `TimeCurve`, a `TimeSeries` that
    covers the plan, or a function in a `TimeFunction`. A bare function is refused, by pyright and by a `TypeError` that
    names `TimeFunction`, and a recorded series that covers fewer steps than the plan raises its `IndexError`. A number
    gives the program it gave, to the last bit. A changing rate adds no variable and no row: the program takes 4.7 ms to
    build with no losses and 8.7 with three functions. A term's `time_factor` and `count_factor` stay functions of the
    game time, read once an interval.
18. **The owner's first round of review, 48 comments, 2026-11-25.** What a comment asked for and needed no decision of
    his was done as it asked; eight questions of design went to him one at a time; and ten threads were answered with no
    change and stay open on GitHub, each with a reply, for him to accept or object to.
    - **Done as asked:** renames; a `PlanItem`'s `duration` and `end`; plainer docstrings ("Your docstrings are
      completely incomprehensible!", on `PlanMoment.units`); two workers a field by default ("I think we only want to
      assign up to two SCVs per mineral field"); a gas building's third worker at about half of the first two, measured
      with `tools/sweep_gas.py` and held in the game-behavior page; `GridSegment`; a dataclass's checks in `_validate`,
      now a rule of the code style; the private constants one function reads written in that function ("I don't
      understand why we introduce module constants for such things"); and `GameTime(minutes=...)`.
    - **A plan's result** ("Maybe call it PlanResult or PlanOutput?", "why `objective` and not `reward` - which one is
      better?"): he chose "BuildPlan + SolveReport". The class keeps its name, what it is worth is its `reward`, and
      `gap`, `is_optimal` and `seconds` are a `SolveReport` it holds.
    - **Wall-clock times** ("Why not timedelta?"): he chose "Float seconds", as `HandlerTiming` has them.
    - **Upgrades in a moment** ("Why are units floats but upgrades a frozenset (i.e. integer)?"): he chose "Shares, in
      both". `PlanMoment.upgrades` maps an upgrade to the share of it done, and `PlanDeviation` gains them.
    - **The walk's name** ("I don't like the name. It needs to indicate that this is specifically for construction"): he
      chose `builder_walk_time`, over `construction_walk_time`, which I recommended.
    - **New bases** ("Actually we should let the user just pass a list of expansions to the planner, and then the
      planner can look up the number of mineral fields and geysers, right?", and to a default: "Yes, but required").
      `plan_now(expansions=...)` takes the expansions not built on yet, in the order the bot takes them, and with none a
      plan starts no command center. A townhall in production needs no entry (2026-11-26: "So not every townhall will be
      built as an expansion, so throwing an error could be a problem", and: "Yes replace the error"): the state holds
      what it brings, a worker build's `mineral_fields` and `geysers`, those of the expansion it stands at or is aimed
      at, and none anywhere else. Where a base brings more fields or more geysers than the one before it, a whole
      variable an interval holds the earlier taken whole first, up to 57 whole variables more and no slower that shows.
    - **A term's weights** ("I think we should name things after the concept that they hold, not how it's implemented"):
      he chose "WeightCurve class, field weights".
    - **A default half-life** ("I think we want to use some `half_life` by default, what do you think?"): he typed "3
      minutes", where I had offered 4, 2 and none.
    - **The long file** ("This file is quite long - can it be logically be broken down?"): he chose "Yes, four modules",
      every method moved as it is, 54 programs the same to the last bit before and after.
    - **The solve's limits** (2026-11-26: "Do you think we should allow the planner more time? E.g. 5 seconds instead of
      3? And should this be linked to the time at which we request a new plan?", and to moving them to the call: "Yes
      all 3"). `time_limit`, `node_limit` and `relative_gap` are `plan_now`'s. The default stays 3 seconds: a wide
      reward is 1.7, 1.1 and 0.6% short of the best after 3 s and 0, 1.1 and 0.6% after 5. Linking the limit to the time
      between plans waits for phase 3.
    - **What he would say of the rest** (2026-11-26: "can you go over this PR again and anticipate any other similar
      comments or suggestions I may have?", and to the eight I listed: "All of them"). Built: `Planner`'s and
      `plan_now`'s docstrings as a plain paragraph an argument; `PlanItem.product`; `Reward.resource_weight`;
      `WeightSegment`; one constructor, `RewardTerm.weighted`; neutral words in the state; `TimeGrid.edges` as
      `GameTime`s; and `ProductionItem`. It showed a fault already there: the default grid's edges, summed in seconds,
      fell a hair short of their game steps, and a plan's times are now put on a whole step where they are within a
      millionth of one.
    - **My own review of the round** (2026-11-26: "Ok do your review first"): ten findings, all in the round's own
      changes, all fixed. The ones that change what a bot sees: an expansion with a townhall on it, anyone's, is left
      out of `expansions`, and only its free geysers count; and a worker's second command center queued reads its second
      order.
    - **Answered with no change:** `deviation_of` stays on the plan; `read_planner_state` stays a function behind
      `Planner.state()`; `LinearProgram` stays private until a second user shows what it should offer; a lower bound
      than 100 starts an interval gained nothing that shows; the planner is Terran's alone for now; a `ProducerActivity`
      and a `UnitCompletion` are no `Progress`; `upgrade_completions` stays a mapping; what each field and geyser has
      left is left out, since the planner pools its workers; and `SECONDS_TOLERANCE` guards sums of floats, not game
      steps.
    - **The guide and this section made shorter** (2026-11-26: "We add a huge amount of documentation text in this pr.
      Can we check if we can make it more concise?", and to cutting both: "Guide and plan file"). The planner's guide is
      a page of its own, `docs/build-planner.md`, which says how to use it and leaves each argument to its docstring;
      this section keeps each decision, its date and his words.
    - **A plan as text** (2026-11-26, of what AvocaDOS will still need, to a printout and a helper for the items left:
      "Two the two smaller things first I think?"). `BuildPlan.to_string()` and `PlanItem.to_string()`, named as
      `GameTime`'s is. The helper for which items of a plan are still to do waits for his word: counting by the state is
      wrong where a unit died or a command center morphed, and a bot that ticks off what it starts needs none.
19. **Gurobi, optional, on its size-limited licence** (the owner, 2026-11-26: "I think we want to support gurobi via the
    free license, making use of highs pre and post solve abilities", "mostly contained to the LinearProgram class", and
    "We would only be running with a single thread"). `Planner(solver=Solver.GUROBI)`, behind `pip install
    sc2nachos[gurobi]`: HiGHS's presolve, Gurobi on one thread, HiGHS's postsolve. The licence counts the program as it
    is handed over, 2000 variables and 2000 rows at most; every benchmark program is over that as built, 2508 to 4471
    rows, and 13 of 14 fit after the presolve, 990 to 1998, the 10-minute `double90` grid's 2896 not. On those 13
    Gurobi proves the plan 5.6 times sooner in the median, 1.6 to 10.7. A program Gurobi refuses is solved by HiGHS
    within the time left, and `SolveReport.solver` says which. highspy comes in for this alone; Open 2 stands for
    HiGHS's own solve.

## Risks

- **Solve time on the full model.** A time limit with the incumbent, the old plan kept when nothing feasible comes
  back, and the item pruning. Measured before anything plays.
- **Model error.** The income model, walks and rounding drift from the game; replanning absorbs drift, the
  collection-rate score calibrates the income, and the simulator of phase 6 measures the rest.
- **Churn.** Plans flipping between near-equal alternatives cost workers walking and money saved for nothing; the old
  plan kept as a candidate, re-scored (Open 2), is not optional, and a hysteresis bonus comes back if phase 3's games
  show the flips cost (decision 9).
- **Execution.** The plan names a barracks, the construction manager must place it; an unplaceable item is a trigger
  and a dropped item, not a stall.
- **Determinism.** Background planning depends on wall-clock speed; the synchronous node-limited mode keeps tests
  and recordings reproducible.
