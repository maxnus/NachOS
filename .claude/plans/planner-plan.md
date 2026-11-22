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
from a toy Terran model of 14 item types solved by scipy's HiGHS on a 32-core machine; the toy is NachOS's
`tools/planner_toy/`, and both were drafted in AvocaDOS and moved with phase 1's pull request.

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
   `whole_starts_until`. A plan's item says whether it is fluid, `PlannedItem.is_fluid` (the owner, 2026-11-19), since
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
   Walk times are per structure, and the bot's (decision 11): `Economy.walk_time`, by default 10 seconds to a command
   center, about the walk from a main's mineral line to its natural on every map of the pool (corpus), and 6 to any
   other structure; the planner reads no path for them. A structure cannot start before its walk unless a worker is
   already on its way. Walks cost Terran 2% and two SCVs.
8. **A plan is executed as an order, not a timetable.** The next item starts when affordable; the predicted times
   are for monitoring and the debug overlay. In AvocaDOS the order maps onto the priorities of the production,
   construction and research objectives, so placement, workers and cancellation stay where they are.
9. **Replanning is parameterised**: an interval in game time, zero for continuous, a wall-time budget per solve,
   event triggers (a structure lost, the forecast changed, an item unplaceable, an attack beginning) and a hysteresis
   bonus for the previous plan's near term. The solve runs in a background process; the bot applies the plan on
   arrival, dropping what it already started. A new plan is never worse only if the old plan is a candidate, by a
   warm start from the shifted previous plan or by re-scoring it, and only with the hysteresis; both are required.
   A synchronous mode with a node limit is deterministic for tests and recordings.
10. **No caps on production structures.** Their number is bounded by the economics; the physical bounds stay: what
    the bot lets a plan start of an item, `start_limits`, its expansions say, with no limit by default, two
    refineries a base, one add-on a barracks, one of each upgrade, supply 200, and room to build near a base once the
    map supplies it.
11. **Every input is visible and the bot's to change** (the owner, review of #239). What the game shows is a public
    `GameState`, which `Planner.game_state` reads and `plan_now(state=...)` takes back changed, a base about to fall
    left out, say; it holds only what the game shows, never an assumption. What the bot assumes is an `Economy`
    handed to each `plan_now`: what each worker at a field or a refinery brings in a second, a list from the first
    worker on, the MULEs' income, a new base's fields and geysers, and the walks; and `loss_rates` and `start_limits`
    beside it, the most a plan may start of a unit type or an upgrade, none for 0 (the owner, 2026-11-19, over
    `new_command_center_limit`, now one entry of it): a plan makes only what the reward values and what that needs,
    so the limits are for what the model adds itself, add-ons, the orbital command and expansions, and a bot that
    opts out by situation hands another mapping to that plan. The constructor keeps only how to solve: the grid, the
    limits and the windows. Workers stay pooled over every field and refinery (Open 10). The plan a new one keeps to
    and the bonus for it are `plan_now`'s `previous_plan` and `hysteresis_bonus` too (the owner, 2026-11-18, over the
    reward holding them, and over the planner keeping to its own last plan, an input the bot would not see): they say
    how plans follow one another, not what the bot wants, so a reward is built once and handed to every plan.

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
- `_game_state.py`: the state as plain values, `GameState`, read from the api when `plan_now` asks: resources, supply,
  the alive count of each type, each item in production with its time left, each structure an SCV builds and whether
  one walks there first, upgrades done and researching, and the fields, refineries and free geysers at the bot's
  bases. A bot may change it and hand it back.
- `_economy.py`: `Economy`, what the bot assumes of its economy (decision 11), with defaults measured in game.
- `_reward.py`: the reward as data the matrix builder reads, and the extension's public interface: per-type weights
  or concave segments, coverage (effectiveness, enemy counts per interval, values), floors, the discount half-life and
  the terminal value. The bot builds it; the planner never guesses it. The hysteresis bonus and the plan it favours
  are `plan_now`'s (decision 11).
- `_milp.py`: the matrix from model, grid, snapshot and reward; the solve with a time or node limit; the plan from
  the solution.
- `_plan.py`: the `Plan`: items in order with predicted start times and the predicted resource and count curves, read
  as `planner.plan`.
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
   `sc2nachos.testing` for the snapshot. A section under "NachOS's own" in `docs/extensions.md`, this plan and the
   toy moved in, and `next-steps.md` pointing here. Exit: the toy's results reproduced from the game's data, and the
   reference configuration, doubling grid with the windows above, solved within 3 seconds on a developer machine.
2. **Playing it**, in AvocaDOS against the checkout. `PlanManager` driving objective priorities, the overlay, and a
   build `BuildOrderName.PLANNER` in `enabled_builds` with a static reward. Games against the built-in AI through
   `scripts/run.py`. Exit: it wins against VeryHard on the pool's maps as the current builds do, and plays a
   recorded game to the end without an error logged. Then a NachOS release and the pin bump, and `/sc2-match`.
3. **Replanning**, in NachOS. The background process, the interval and continuous mode, the triggers, the
   hysteresis, and the warm start through highspy as an optional dependency if its wheel is accepted into
   AvocaDOS's ladder zip (`LOCKED_WHEELS` in `scripts/release_helper.py`). Exit: AvocaDOS's step time unchanged
   with planning on, and a release on the ladder.
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

1. **NachOS's scope line: settled** (the owner, 2026-11-07). The planner is in NachOS's own extensions, and the
   first PR amends the line narrowly: a solver any bot hands its objective is in scope, as pathfinding is a solver
   over the map, while the objective itself, strategy, build orders and economy rules, stays the bot's. The
   alternative the extensions guide names, a package of its own as `sc2nachos-planner`, was weighed and set aside:
   NachOS releases often, the model's coupling to the game data is tested in-tree, the test corpus is there, and the
   public-api test keeps a later split a day's work.
2. **highspy.** Its warm start is what makes continuous replanning strictly better and the budget sub-second, at
   the price of a second solver wheel beside scipy's, in NachOS as an optional dependency and in AvocaDOS's zip.
   Phase 3 decides it with a measurement.
3. **The item set: a wide reward is not solved in 3 seconds** (measured in phase 1's pull request, 2026-11-18,
   `tools/benchmark_planner.py items`). The real model has more types than the toy's 14, and integers scale with
   them: a reward on 13 army types and 8 upgrades brings 38 items and 1,494 whole variables, the upgrades' 504 of
   them, where the reference has 14 and 419. From the game's start it finds no plan within 1 s at any half-life; after
   3 s none at a half-life of 120 s, and plans 5% short of the best undiscounted, 28% at 240 s and 24% at 480; after
   10 s plans 0.8 to 1.6% short; and it is proven optimal in 36 to 38 s at three half-lives, 2.1% from its bound after
   40 at 240 s. A replan in the middle of a game with the toy's 14 items is no such trouble (`replans`, from two
   made-up states at 3:00 and 6:00 with units in production, a reward that never saturates): a plan within 1.1% of
   the best after 1 s and within 0.3% after 3, proven optimal in 4 to 18 s.

   Since 2026-11-19 the default times follow the state's income and an upgrade has one by its cost (decision 3, the
   owner), which leaves the reference as it was, a game's start with no upgrade. The replans have 245 to 265 whole
   variables, from 419: a plan within 0.9% of the best after 1 s and within 0.06% after 3, proven optimal in 2.1 to
   5.2 s. The wide reward has 1,291 from the game's start, the upgrades' 301 of them, and 813 and 729 from the two
   mid-game states (`items`, which plans from them too since). From the start it is proven optimal in 14 to 30 s; after
   1 s it has no plan at 120 s and plans 1.7 to 3.3% short at the others, after 3 s the best plan undiscounted and
   plans 0.6 to 6.5% short at the others, after 10 s plans at most 1.0% short. From 3:00 and 6:00 it has a plan after
   1 s in one case of eight, after 3 s plans within 0.5% of the best, and is proven optimal in 7 to 18 s; before, by
   a script outside the package, it was 0.4 to 1.1% from its bound after 40. What the shorter times cost was judged
   by the program with the times before: a plan's starts of the first 30 seconds fixed in it, the rest solved anew
   within 60 s, against the best plan known. The plans found in 3 s lose 0.02 to 0.09% so for the replans, where
   those by the times before lose 0.00 to 0.21%, and 0.11 to 0.43% for the wide reward from mid-game, where those
   lose 0.45 to 1.84%; the plans proven optimal lose at most 0.14%, within what the judge's own unproven solves
   leave. A wide reward's plan after 3 s from the start, 28% short by its objective before, lost 1.2% so: most of
   what it lacked was in the far term. Dividing a type's time by how many the state holds was measured beside these
   (decision 3): 306 to 332 whole variables for the replans alone, 233 to 259 with the income's 245 to 265, and no
   faster with it.

   An upgrade done or being researched stays an item, its starts held at none, and so does what only it needed, an
   engineering bay. Leaving those out changes nothing that shows: with the wide reward's eight upgrades done at 3:00
   and at 6:00, 813 and 729 whole variables against 610 and 554 without their nine items, the same objectives, and
   over six half-lives of each 87 s of proofs in all against 86, the faster of the two in five cases and the slower
   in seven (a script outside the package, one run each). What a bot would not build though a plan would is
   `start_limits`' (decision 11), the item kept and its starts held to the limit: a program without the item lost
   what bound those the state holds, and planned for more than it could have. With none of reactors and the orbital
   command, by a script outside the package, one run each: the reward of `replans` from a game's start is proven
   optimal in 2.4 to 2.9 s from 4.1 to 5.3 and the wide one in 7.5 to 15.3 s from 24.3 to 30.6, their plans worth 2.4
   to 4.0% less for what they go without; from 3:00 and 6:00 in 2.8 to 3.6 s from 2.7 to 5.2 and in 6.5 to 10.4 s
   from 8.2 to 15.5, worth at most 0.2% less.

   On the grid of decision 2 since 2026-11-19, 57 intervals: the replans have 292 and 274 whole variables, a plan
   within 0.15% of the best after 1 s and within 0.08% after 3, and are proven optimal in 1.8 to 3.6 s. The wide
   reward has 1,202 from the game's start, 860 from 3:00 and 787 from 6:00, and a plan after 1 s in eleven cases of
   twelve: from the start 2.9 to 6.4% short after 1 s and at most 1.1% after 3, proven optimal in 5.1 to 13.1 s; from
   3:00 and 6:00 at most 0.8% short after 1 s and 0.16% after 3, proven in 5.5 to 11.2 s. When the grid first doubles
   was chosen by a script outside the package, each plan's first 30 seconds, or 24, fixed in a grid of 34-step
   intervals to 2,176 steps: doubling from 544 steps, 816, 1,088 and 1,632 gave 57, 73, 89 and 121 intervals and proofs
   of the reward of `replans` from a game's start in 1.8 to 2.1 s, 2.7 to 3.6, 7.1 to 7.2 and 18.6 to 22.9, where
   2-second intervals took 4.1 to 5.3, and none lost more than 0.07% so, where the plans on 2-second intervals lost 0.5
   to 1.5% from a game's start; from mid-game all were within 0.5%, doubling from 544 within 0.11%. The wide reward
   from a game's start at a half-life of 240 s was proven in 14.0 s doubling from 544, and not in 40 from 816 on.

   Open: prune to what the reward reaches, or phase 3's start from the previous plan. Where no plan is found in
   time, as for a wide reward's replan at 1 s in one case of twelve, `plan_now` returns `None` and `plan` stays the
   one before (the owner, 2026-11-19), where it raised a `NoPlanFoundError`: a bot keeps the plan it has, and the
   type makes it check.
4. **Build space.** A bound on structures per base from the map, as the one physical limit the model lacks;
   `api.placement` may answer it.
5. **Replanning defaults.** The interval, the budget and the hysteresis bonus are parameters; their defaults come
   out of phase 3's games.
6. **The discount against the solver.** A discount made a 7-minute solve faster and a 10-minute one slower; settle
   it on the real model.
7. **Phase 1's exit, the reference configuration solved within 3 seconds, is met since the grid of 34 steps: proven
   optimal in 1.5 to 2.2 s** (measured in phase 1's pull request). First, with each item's starts an interval at a time
   and decision 3's windows by cost, the concave reward was proven optimal in 8 to 10 s at every half-life, and the plan
   found after 3 s was worth 0.5 to 0.8% less than the optimal one, the solver's bound still 2.0 to 2.5% above it; the
   toy's linear reward was optimal in 8 s, but 6.5% short after 3. With phase 1's first windows, townhalls, production
   and tech structures whole over the horizon, the concave reward was 7.6% from its bound after 3 s at a half-life of
   240 s and 12% undiscounted, still 7 to 11% after 10 s: the root relaxation of the whole structures is what stayed
   loose, and a tighter big-M did not help. Left for the owner: the incumbent at 3 s, or phase 3's warm start. With no
   limit on new command centers, an interval's are bounded by 100, and the prerequisite rows' big-M of a morph and a
   refinery with them, to about 101 and 202 at a game's start, where a limit of three gave 4 and 8; the reference was no
   slower, optimal in 7.0 to 11.0 s. A valid tighter bound, if the 3 seconds call for one: the command centers the
   horizon's resources can pay for.

   Since 2026-11-18 the program's variables are each item's starts so far by each interval's end, an interval's starts
   being those less the interval's before: the same program in other variables, in #239 (the owner, over a pull request
   of its own). A row that counts what has started or is done by a time then takes one variable an item, where it took
   every interval's before: the reference program's 87,024 coefficients are 14,417, in 3,019 rows from 2,137, and it is
   proven optimal in 3.9 s undiscounted, 2.9 at a half-life of 120 s, 4.2 at 240 and 2.5 at 480, where it took 9.2,
   10.4, 8.4 and 8.5. After 3 s the plan is the optimal one at 120 and 480 s, 0.05% short undiscounted and 0.7% at
   240. The best plan's worth is unchanged at each; of plans worth the same the solver now returns another, and three
   tests that counted on which were made to count only the units done by the horizon.

   Since 2026-11-19 the exit is met: on the grid cut to a worker's build time (decision 2), 57 intervals and 401 whole
   variables where it had 63 and 419, the reference is proven optimal in 1.6 s undiscounted, 1.8 at a half-life of
   120 s, 1.5 at 240 and 2.2 at 480, the same to 0.02 s in three runs. Its objectives are 2.5 to 6.2% above those on
   2-second intervals, of another grid and so no measure, but in the direction of an SCV every 12.14 seconds for every
   14. The owner's question of the proof or the plan found by 3 seconds no longer decides the exit.
8. **The count-form cut holds only where an interval's starts are whole** (phase 1, for the owner to confirm). The toy
   applied it in every interval shorter than every build in it, fluid ones too, where it is no valid cut: in 16-second
   intervals a barracks made a marine every 32 seconds, not every 17.86. Held to whole intervals, the toy's linear case
   was worth 6.4% more, with 7 barracks instead of 13.
9. **Production chained on one producer starts a whole number of intervals apart while its starts are whole**, since a
   start sits on an interval's first second. For workers the grid answers it since 2026-11-19 (decision 2): on 2-second
   intervals an SCV started every 14 seconds, and every 16 on 4-second ones, against 12.14 in game, which the toy's
   12-second SCV hid; on intervals of 34 steps it starts every 272 steps. What is left is every build no interval
   divides, up to an interval longer each: a marine's 400 steps planned as 408, 2%, where 2-second intervals had 0.8%,
   and a marauder's or a supply depot's 480 as 510, 6%, where they had 2.7%. Only intervals of 16 steps divide every
   build, at 2.5 times the whole variables. Starts queued behind the build ahead would answer the rest, and are not to
   be built (the owner, 2026-11-19): what exact chains could add was measured by a script outside the package, a grid of
   16-step intervals until 4,352 steps, on which every chain is exact while its starts are whole, against one of 34-step
   intervals of the same shape. The reward of `replans` was worth 0.07% more on it from 6:00 at a half-life of 240 s and
   0.05% undiscounted, both proven, and at least 0.03% more from a game's start, unproven after two minutes, the finer
   intervals' every other gain included. Against that, a queued start is done by its place in the queue and not by its
   interval, so each item a producer makes would need a count done by each interval, whole and so more integers, or
   fluid and so 1.9 marines done; and a barracks queues marines and marauders together, which sums cannot order.
   Per-item interval lengths were weighed too: grids of other lengths merge to nearly 16-step intervals, and each item
   starting only on a doubling of 34 steps of its own, a share of its build time, left 18 to 66% fewer whole variables
   free, proved the reward of `replans` no sooner, and halved the wide reward's proof for plans worth 0.7 to 1.4% less,
   the one grid's plan the better after 3 s in all eight cases. What is built instead rounds either way (the owner,
   2026-11-20, to whom a marauder's 480 steps planned as 544 on 68-step intervals, where 476 is seven of them, was
   plainly wrong): a whole start, or a build under way, that runs into an interval by less than a share of it leaves the
   interval free, `Planner(max_ignored_overrun=...)`, by default a quarter, 0 for none and a half for the nearest
   interval; the owner chose a parameter and the quarter over the nearest, so that a plan runs ahead of the game by a
   quarter of an interval at most, 0.4 s on the default grid. On it a marauder is planned every 476 steps, a siege tank
   every 714 for its 720 and a thor every 952 for its 960, a marine every 408 as before; fluid starts keep their build
   time, and a build counts as done from the first edge at or after its end as before.
10. **Workers per base** (the owner, review of #239: to come back to). The planner pools workers over every field
    and refinery, so a new SCV mines from when it is done, even one that has to walk to another base, about 10
    minerals too many each, and a new base's fields count from when its command center is done. Separate workers per
    base, with transfers, would roughly double the reference program's 1388 variables and add some 125 integers to its
    419, whether each future base stands yet, with the solver already short of optimal at 3 seconds; unmeasured. A
    cheaper middle way, a delay the `Economy` sets before each new SCV mines, was left for this discussion.
11. **What an item needs first is whole** (the owner, 2026-11-20: built, and the bot's to set). The prerequisite rows
    hold an item's starts in an interval at most the interval's most times the count granting its tech, a switch while
    that count is whole and a dial once it is fluid: a hundredth of a factory unlocked a starport. The reference plan
    from a game's start listed a starport at 4:03 and five medivacs and never a factory, 0.00 of one alive at its
    end; a wide reward's plans made six ghosts on 0.02 of a ghost academy and thors on half an armory. It changes
    what a bot does: from the state of `replans` at 3:00 with 600 minerals and 300 vespene more, the plan had its
    first starport at 1:49, its first medivac at 2:50 and no factory, where needing a whole factory it starts one at
    0:11, the starport at 0:55 and the first medivac at 1:31; each replan choosing alike, the factory is never built.
    `Planner(unlock_fraction=1.0)`, the default, adds for each tech no structure of the state grants, and each
    upgrade another needs, a 0 or 1 in each interval, 1 only where that fraction of one is done, which holds the item's
    starts instead; it is whole only from the edge at which a fluid start of what it counts can be done, the count
    being whole before. It was first an on or off, `whole_prerequisites`; the owner (2026-11-21) made it a fraction
    and named it, the planner to offer many arguments since nobody knows yet how it works best: under 1 a plan pays
    for that share of a structure alone, under a half for less than it lists as an item, and with 0 no 0 or 1 is
    added and the program is what it was. A half solves as a whole does: `reference` in 14.3 s in all against 13.8,
    `replans` 32.1 against 32.6 and `items` over 5 minutes 104.6 against 107.1, where 0 took 8.3, 29.3 and 46.0.
    Measured (`tools/benchmark_planner.py --time-limit 30`, the runs side by side): `reference` 465 whole variables
    from 401, proven in 2.3 to 4.4 s from 1.7 to 2.5; `replans` 300 to 316 from 274 to 292, 2.2 to 6.5 s from 2.4 to
    5.0; `items` 910 to 1,335 from 787 to 1,202, none of twelve proven in 30 s, 0.2 to 5.1% from the bound, where all
    were in 5.0 to 14.6 s. Over 6 minutes, by a script outside the package that sets the benchmark's horizon:
    `reference` 1.3 to 1.9 s, `replans` 1.7 to 4.0, `items` 2.6 to 5.5 s from a game's start and one of eight proven
    from mid-game; over 5 minutes 1.4 to 2.5, 1.5 to 4.7, and `items` 1.8 to 3.1 s from a game's start, 13.5 to 20.3
    from 3:00 and 6.4 to 11.2 from 6:00. The 0 or 1 of an upgrade another needs costs little: without it `items` over
    5 minutes took 101.7 s in all against 107.5. Keeping every structure that grants a needed tech whole throughout
    closed the flaw too, at more cost: the reference from a game's start was not proven in 30 s. Also measured then,
    by scripts outside the package, each plan's first 30 seconds fixed in a 10-minute program and the rest solved
    anew: an item fluid no later than its producer, or every product of a producer fluid together, changed what those
    30 seconds were worth by 0.3% at most and the solve little; a producer whole as long as its products made the
    solve slower and the plan no better; a horizon of 5 or 6 minutes lost 0.3% at most of those 30 seconds; first
    intervals of 68 steps about halved the solve of the reward of `replans` for 0.2% at most; and whole times twice as
    long bought nothing. Open: a wide reward from mid-game, which an unlock fraction above 0 leaves unproven in 3 s
    over any horizon tried.
12. **`plan_now` plans over 5 minutes by default** (the owner, 2026-11-21, who first asked whether 5 or 6 would do,
    2026-11-20), where the horizon had no default and every measurement before was over the toy's 10. Measured by a
    script outside the package with an unlock fraction of 1, nine cases, each horizon's plan after 3 s with its first
    30 seconds fixed in the 10-minute program and the rest solved anew: against the best of them, 6 minutes lost 0.00
    to 0.38%, 5 minutes 0.00 to 0.73%, 4 minutes 0.00 to 0.23% and 3 minutes up to 5.09%, 3.95% on the reference's
    reward from a game's start; so 4 to 6 minutes plan the next 30 seconds alike, and 3 do not. The reward of
    `replans` was proven in 0.9 to 4.9 s over 5 minutes, 1.2 to 3.8 over 6 and 2.3 to 6.8 over 10; the wide reward in
    2.0, 17.9 and 6.7 s over 5 minutes, and over 10 in none of its three cases within 30 s. What that measure cannot
    see is what pays back late: from a game's start, undiscounted, the 10-minute plan for marines, marauders and
    medivacs starts a command center at 0:49, and the plans over 6, 5 and 4 minutes none, nor with the end state
    counted 5 minutes longer, since the reward values no income; each replan choosing alike, a bot stays on one base.
    The owner: the expansion is built if the bot adds it to the reward. Over 5 minutes, 1 a second for each SCV
    beside that reward starts the command center at 0:49 too, and 20 a second for each command center starts three,
    at 0:36, 1:13 and 1:37. `tools/benchmark_planner.py` plans `reference`, `replans` and `items` over the 5 minutes
    since, and any case over `--horizon` seconds; `--horizon 600` is what every measurement above was made over.
    Phase 1's exit (Open 7) with it: the reference is proven in 1.4 to 2.5 s over 5 minutes, and with an unlock
    fraction of 1 in 2.2 to 4.4 s over 10.

13. **A plan says how its workers split, and moving one costs time** (the owner, 2026-11-22: "we definitely want to
    report this in the plan and we definitely want to have an interruption when reassigning a worker, before the new
    income kicks in. Something like 2 or 3s would be enough"). The split between minerals and vespene was the plan's
    choice in each interval all along, moved for nothing, and a `Plan` did not hold it. It holds `miners` and
    `collectors` now, one number an interval, and `Economy.reassignment_time`, 3 seconds by default (assumed, the higher
    of the owner's two), is how long a worker moved to a refinery collects nothing and one moved from a refinery neither
    mines nor builds; 0 moves them for nothing as before, and adds no variable. Every rise of the collectors in a tier
    of rates, and every fall of them in all, is a move, from `GameState.vespene_collectors` on, which
    `Planner.game_state` reads as the workers the game counts as assigned to the refineries; a new SCV's first walk to a
    refinery is one too, a new SCV's to a field is not, and the fall of those lost is not. A move made at an interval's
    start runs through the intervals its time spans, so it costs its seconds on any grid. A fall that what is now
    forces, fewer SCVs left beside the builders under way than collect, costs nothing, so that a plan starting nothing
    more stays possible. Measured on the planner's defaults, `--time-limit 30`, side by side with the reference
    economy's time set to 0 by a script outside the package: `reference` proven in 1.8 to 2.1 s, with 0 in 1.4 to 2.5,
    worth 0.26 to 0.34% less; `replans` in 1.6 to 3.7 s, 21.2 in all, with 0 in 1.5 to 4.7, 23.3 in all, worth 0.00 to
    0.13% less; `items` all twelve in 2.8 to 18.0 s, 114.0 in all, with 0 in 1.9 to 20.4, 112.2 in all, worth 1.08 to
    1.24% less from a game's start and 0.14 to 0.37% less from mid-game. So the solve is no slower beyond the noise, and
    a plan is worth less by about what 3 seconds later vespene delays. The reference plan and the wide one from 3:00
    split their workers as before: each refinery takes three from when it is done and keeps them. Open: the 3 seconds
    are assumed, not measured in game; where what a worker would bring buys nothing before the horizon a plan leaves it
    idle, as in its last one or two intervals, so a bot reads the first interval's `collectors` and has the rest mine.

    **Every start of mining pays too** (the owner, the same day: "I think workers immediately assigned to mining
    should pay too"), which is the cheaper middle way of Open 10. A worker that starts to mine or to collect brings in
    nothing for the time: every rise of the workers in a tier of rates, mining or collecting, is such a start, a new
    SCV's first walk to a field too. A worker from a refinery pays as it starts to mine, where it idled before, and one
    that leaves for nothing pays nothing, so no fall is forced to cost and the room kept for forced falls is gone. A
    builder back from its structure starts for nothing, its walk back being the builder's already: of the miners that
    start in an interval, as many as builders' shares of it fall by. The plan starts from the state's collectors and
    takes every other SCV that builds nothing to be mining, so the state gained no field for it. The time is lost of
    the interval a worker starts in and those after it, never a whole interval: in a 24-second interval, 3 of its 24
    seconds. Measured as above, against 0: `reference` proven in 1.9 to 3.0 s, 9.3 in all, with 0 in 1.4 to 2.5, 7.2
    in all, worth 0.64 to 0.82% less; `replans` in 2.0 to 5.8 s, 28.3 in all, with 0 in 1.5 to 4.8, 23.1 in all,
    worth 0.00 to 0.27% less; `items` all twelve in 2.5 to 22.6 s, 120.2 in all, with 0 in 1.8 to 20.0, 109.5 in
    all, worth 1.35 to 1.46% less from a game's start, 0.43 to 0.56% from 3:00 and 0.13 to 0.16% from 6:00. So the
    solve is a tenth to a quarter slower in all, single solves within their noise, and the reference at one half-life
    took 2.96 s of phase 1's 3. A command center after uninterrupted SCVs is paid for one SCV later, the sixth's start
    where it was the fifth's.

    **A plan holds its intervals, not a tuple for each value** (the owner, the same day: "Would it not be better to
    have a new class that represents all these values for a single bucket and then have a single tuple over objects
    of this class?"). The stocks and counts had a value for each edge of the grid and the workers one for each
    interval, one fewer. `Plan.intervals` is a tuple of `PlanInterval`: `start`, `end`, the `miners` and `collectors` in
    it, and the `minerals`, `vespene` and `counts` expected at its end, the last interval's being the horizon's. The
    first edge is gone and held nothing: on the reference reward from 0:00, 3:00 and 6:00 its stocks and counts were
    the state's to the last digit, and each interval's end the next one's start to 1e-14. Intervals stay, with a start
    and an end, rather than points in time, whose last, the horizon, would have no workers (the owner, the same
    day), and `Plan.interval_at(time)` finds the one a time is in; what the stock or a count is between two edges is
    the bot's to estimate. **An interval was a bucket** until the owner renamed it the same day ("I don't really like
    the name"), everywhere in the planner, its tests, its benchmark and the docs: `PlanInterval`, `Plan.intervals`,
    `Plan.interval_at`, and `TimeGrid.doubling`'s `initial_interval_length`, `initial_interval_count` and
    `max_interval_doublings`. Bucket is lot-sizing's word and interval optimal control's. The toy in
    `tools/planner_toy/` keeps its own word, and the replanning interval of decision 9 is another thing. Seen while
    moving the tests over: where minerals buy nothing more, a plan may count more starts of mining than its miners
    rise by, which
    costs it nothing, so its stock reads less than its miners would bring in. Twelve SCVs and one more to train over
    14 seconds read 8 miners in the first interval and no minerals at its end: 16 starts counted in the tier at half
    rate, where no worker mined, took the 17 minerals the 8 brought in.

    **An interval runs from one moment to the next** (the owner, the same day, to six questions on its shape). A
    `PlanMoment` holds what the planner expects at an edge of the grid: its `time`, the `resources` in stock as the
    library's `Resources`, the `units` of each type alive, and the `upgrades` done, which a plan did not hold. A
    `PlanInterval` holds its `start` and its `end`, two moments, with its `miners` and `collectors`; one interval's
    end is the next one's start, the same object, so no edge is spelled twice, and the first interval's start is the
    state again. `GameState` reads alike: `resources` for `minerals` and `vespene`, and `units` for `counts`. The
    units are every type the plan makes and those of the state and its production, where they were the plan's
    alone. An upgrade reads as done from half of it, where its start is fluid, as a fluid item is listed from half
    of one. Decided against, for now: an interval holding its items, since a bot carries a plan's items out in
    order rather than by interval, and a fluid item is listed where its fractions add up, not where the plan pays;
    and a generic `PlannedItem`, since pyright narrows `planned.item` by `isinstance` already and does not narrow
    `planned` with it.

    **A reward's weights hold its curves** (the owner, the same day, of the two: "Isn't the latter just a more general
    form of the former?"). `Reward.curves` is gone: `Reward.weights` holds for each unit type one number or a list of
    `CurveSegment`, so no type can be given both. A segment's `slope` is its `weight` and its `up_to` its
    `up_to_count`, names the owner asked to be bettered and has not yet seen. A curve of one piece goes into the
    objective as a number does, with no variable.

    **The stock left at the horizon is worth a little** (the owner, the same day, to the fix proposed for it):
    `Reward.stock_weight`, by default 0.0001 a mineral and a vespene, once and not discounted. Income is then never
    worth nothing, so no start is counted that no worker makes and no worker stands idle, the last intervals'
    included, which closes that half of Open 13. A weight of 0.00001, 0.0001 or 0.001 did the same on the growing and
    the wide reward from 0:00, 3:00 and 6:00: no phantom start, where the growing reward from 6:00 counted 12.1 with
    0, and no SCV idle in the last interval, where all 18 to 36 were. Measured with `--time-limit 30` against 0, every
    solve proven: `reference` in 1.6 to 2.0 s, 6.9 in all, with 0 in 1.8 to 2.8, 8.7; `replans` in 2.1 to 4.4 s, 22.0
    in all, with 0 in 1.8 to 5.4, 26.7; `items` in 2.4 to 22.0 s, 112.7 in all, with 0 in 2.4 to 21.4, 114.1. The
    reward less the stock's worth is the same to the cent on `reference` and within 0.01% on the others, 0.009% less
    to 0.010% more, which is the solver's gap. The stock at the horizon is 559 to 6741 where it was 12 to 32. What it
    changes beside: a plan that needs no more vespene mines rather than collects while a field brings in more than a
    refinery, and the weight is in a plan's `objective`. Open: the default suits weights around 1, and a reward of
    much smaller ones wants it smaller, which nothing checks.

## Risks

- **Solve time on the full model.** A time limit with the incumbent, the old plan kept when nothing feasible comes
  back, and the item pruning. Measured before anything plays.
- **Model error.** The income model, walks and rounding drift from the game; replanning absorbs drift, the
  collection-rate score calibrates the income, and the simulator of phase 6 measures the rest.
- **Churn.** Plans flipping between near-equal alternatives cost workers walking and money saved for nothing; the
  hysteresis bonus and a warm start are not optional.
- **Execution.** The plan names a barracks, the construction manager must place it; an unplaceable item is a trigger
  and a dropped item, not a stall.
- **Determinism.** Background planning depends on wall-clock speed; the synchronous node-limited mode keeps tests
  and recordings reproducible.
