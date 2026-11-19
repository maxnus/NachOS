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

1. **A time-bucketed MILP, solved by HiGHS through scipy.** Starts of each item per bucket are integer or fluid
   variables; minerals, vespene and supply are linear flows; producers, workers and prerequisites are linear
   constraints. Scipy is already a dependency and HiGHS is among the best open-source solvers; a solver of our own
   would not beat it, and the Python side of a solve costs under 0.2 s. The lever is the formulation, below. No
   compiled code of our own: the ladder zip takes compiled packages from locked manylinux wheels, and an extension
   of ours would need a wheel built in CI.
2. **A grid that widens with time, cut to a worker's build time: buckets of 34 game steps doubling from 544** (the
   owner, 2026-11-19, over 2-second buckets doubling every 45 seconds, 63 in all), 68 until 1,088, 136 until 2,176,
   272 until 4,352 and 544 after, 1.5 seconds to 24.3, 57 buckets by ten minutes. Ten-second buckets undertrained
   workers, an SCV every 20 to 30 seconds instead of every 12, and undervalued the plan by 5%; 2 seconds was within
   0.01% of 1 second at half the solve time, but still trained an SCV every 14 seconds for the game's 12.14, a start
   sitting on a bucket's first second (Open 9). Every build time is a whole number of game steps, and of 16: a
   worker's 272, of every race, are eight buckets of 34, and stay a whole number of buckets while the buckets double
   to 272, so SCVs chain as in the game; 2 seconds are 44.8 steps and divide nothing. A doubling grid keeps the
   relative timing error constant along the plan. Durations are never rounded: a build enters the producer
   constraints in exact seconds split across the buckets it overlaps; only a start sits on a bucket's first second
   and a completion on the next edge. A bot hands `Planner` any other `TimeGrid`. `TimeGrid.doubling` takes
   `initial_bucket_length`, `initial_bucket_count`, an even number, and `max_bucket_doublings` (the owner,
   2026-11-19, the names too), counts where it took the time of the first doubling and the longest bucket: a time
   need not be a whole number of buckets, and the grid then doubled at the last edge before it, at 0:44 for the 0:45
   it was asked, which a grid cut to a worker cannot afford.
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
   `whole_starts_until`. A plan's item says whether it is one, `PlannedItem.is_fluid` (the owner, 2026-11-19), since
   a fluid one stands for fractions summed and rounded. The rule: an item stays whole where its cost exceeds a bucket's income and it is made once a
   bucket at most. The owner's question behind the income was whether a type the state holds many of could be fluid
   sooner, 60 workers against 12: a time divided by the count of the type gained little alone and nothing beside the
   income's, which already brings most items to the 30 seconds, and was not built (Open 3 holds the measurements).
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
5. **The enemy enters as per-bucket constants.** Coverage, each enemy type worth covering up to its count by what
   the own army is worth against it from the unit profiles' effectiveness matrix, is a minimum and so concave; it
   flipped the toy from 50 marauders against armored to 168 marines against air, and ramped marines ahead of air
   forecast for 4:00. Scenarios combine by expectation or by worst case, both concave. A bad matchup is a linear
   penalty or a loss rate, never a capped one.
6. **Expected losses are survival factors on completions**, per type and bucket, from recent deaths, the matchup and
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
| 7 min, 10 s buckets, 588 integers, integrated reward | usable plan in 1 s, proven within 5% in 4 s, 4% gap left at 60 s |
| same, terminal reward | 10 to 30 s for a sensible plan |
| 10 min, uniform 10 s, 840 integers | 24% gap after 10 s |
| 10 min, widening grid, integer to 1:30, 126 integers | proven optimal in 1.2 s |
| 10 min, 2 s near term, integer to 1:30, 630 integers | optimal in 4.2 s; 1 s buckets, 1260 integers, in 7.9 s |
| 10 min, doubling grid, 476 integers | optimal in 5.8 s |
| big structures whole to 10:00, +112 integers | 0.5% gap at 10 s, optimal at 25 s, same near-term plan |
| concave reward | a mix of types; 9% gap at 10 s instead of 0.5% |
| all-fluid LP | 10 ms, 3% above optimal, fractions of a command center over three buckets, no rounding feasible |
| Python matrix build | 18 to 150 ms |
| the planner's reference, starts held as running totals | optimal in 2.5 to 4.3 s, from 8.4 to 10.4 |
| replans from 3:00 and 6:00, times by the state's income, 245 to 265 integers from 419 | optimal in 2.1 to 5.2 s, from 4.2 to 18.2 |
| a wide reward, 38 items, upgrades' times by cost, 1,291 integers from 1,494 | optimal in 14 to 30 s, from 36 to 38 and a 2.1% gap at 40 s |
| the planner's reference on buckets of 34 steps doubling from 544, 57 buckets, 401 integers | optimal in 1.5 to 2.2 s, from 2.5 to 4.3 |
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
- `_grid.py`: bucket edges from segments. Each item's integrality window comes from its cost, or from the bot.
- `_game_state.py`: the state as plain values, `GameState`, read from the api when `plan_now` asks: resources, supply,
  the alive count of each type, each item in production with its time left, each structure an SCV builds and whether
  one walks there first, upgrades done and researching, and the fields, refineries and free geysers at the bot's
  bases. A bot may change it and hand it back.
- `_economy.py`: `Economy`, what the bot assumes of its economy (decision 11), with defaults measured in game.
- `_reward.py`: the reward as data the matrix builder reads, and the extension's public interface: per-type weights
  or concave segments, coverage (effectiveness, enemy counts per bucket, values), floors, the discount half-life and
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

   On the grid of decision 2 since 2026-11-19, 57 buckets: the replans have 292 and 274 whole variables, a plan
   within 0.15% of the best after 1 s and within 0.08% after 3, and are proven optimal in 1.8 to 3.6 s. The wide
   reward has 1,202 from the game's start, 860 from 3:00 and 787 from 6:00, and a plan after 1 s in eleven cases of
   twelve: from the start 2.9 to 6.4% short after 1 s and at most 1.1% after 3, proven optimal in 5.1 to 13.1 s; from
   3:00 and 6:00 at most 0.8% short after 1 s and 0.16% after 3, proven in 5.5 to 11.2 s. When the grid first doubles
   was chosen by a script outside the package, each plan's first 30 seconds, or 24, fixed in a grid of 34-step
   buckets to 2,176 steps: doubling from 544 steps, 816, 1,088 and 1,632 gave 57, 73, 89 and 121 buckets and proofs
   of the reward of `replans` from a game's start in 1.8 to 2.1 s, 2.7 to 3.6, 7.1 to 7.2 and 18.6 to 22.9, where
   2-second buckets took 4.1 to 5.3, and none lost more than 0.07% so, where the plans on 2-second buckets lost 0.5
   to 1.5% from a game's start; from mid-game all were within 0.5%, doubling from 544 within 0.11%. The wide reward
   from a game's start at a half-life of 240 s was proven in 14.0 s doubling from 544, and not in 40 from 816 on.

   Open: prune to what the reward reaches, or phase 3's start from the previous plan; and what a bot is handed when
   no plan is found in time, where `plan_now` raises `NoPlanFoundError`, as a wide reward's replan still does at 1 s
   in one case of twelve.
4. **Build space.** A bound on structures per base from the map, as the one physical limit the model lacks;
   `api.placement` may answer it.
5. **Replanning defaults.** The interval, the budget and the hysteresis bonus are parameters; their defaults come
   out of phase 3's games.
6. **The discount against the solver.** A discount made a 7-minute solve faster and a 10-minute one slower; settle
   it on the real model.
7. **Phase 1's exit, the reference configuration solved within 3 seconds, is met since the grid of 34 steps: proven
   optimal in 1.5 to 2.2 s** (measured in phase 1's pull request). First, with each item's starts a bucket at a time and
   decision 3's windows by cost, the concave reward was proven optimal in 8 to 10 s at every half-life, and the plan
   found after 3 s was worth 0.5 to 0.8% less than the optimal one, the solver's bound still 2.0 to 2.5% above it; the
   toy's linear reward was optimal in 8 s, but 6.5% short after 3. With phase 1's first windows,
   townhalls, production and tech structures whole over the horizon, the concave reward was 7.6% from its bound after
   3 s at a half-life of 240 s and 12% undiscounted, still 7 to 11% after 10 s: the root relaxation of the whole
   structures is what stayed loose, and a tighter big-M did not help. Left for the owner: the incumbent at 3 s, or
   phase 3's warm start. With no limit on new command centers, a bucket's are bounded by 100, and the prerequisite
   rows' big-M of a morph and a refinery with them, to about 101 and 202 at a game's start, where a limit of three
   gave 4 and 8; the reference was no slower, optimal in 7.0 to 11.0 s. A valid tighter bound, if the 3 seconds call
   for one: the command centers the horizon's resources can pay for.

   Since 2026-11-18 the program's variables are each item's starts so far by each bucket's end, a bucket's starts
   being those less the bucket's before: the same program in other variables, in #239 (the owner, over a pull request
   of its own). A row that counts what has started or is done by a time then takes one variable an item, where it took
   every bucket's before: the reference program's 87,024 coefficients are 14,417, in 3,019 rows from 2,137, and it is
   proven optimal in 3.9 s undiscounted, 2.9 at a half-life of 120 s, 4.2 at 240 and 2.5 at 480, where it took 9.2,
   10.4, 8.4 and 8.5. After 3 s the plan is the optimal one at 120 and 480 s, 0.05% short undiscounted and 0.7% at
   240. The best plan's worth is unchanged at each; of plans worth the same the solver now returns another, and three
   tests that counted on which were made to count only the units done by the horizon.

   Since 2026-11-19 the exit is met: on the grid cut to a worker's build time (decision 2), 57 buckets and 401 whole
   variables where it had 63 and 419, the reference is proven optimal in 1.6 s undiscounted, 1.8 at a half-life of
   120 s, 1.5 at 240 and 2.2 at 480, the same to 0.02 s in three runs. Its objectives are 2.5 to 6.2% above those on
   2-second buckets, of another grid and so no measure, but in the direction of an SCV every 12.14 seconds for every
   14. The owner's question of the proof or the plan found by 3 seconds no longer decides the exit.
8. **The count-form cut holds only where a bucket's starts are whole** (phase 1, for the owner to confirm). The toy
   applied it in every bucket shorter than every build in it, fluid ones too, where it is no valid cut: in 16-second
   buckets a barracks made a marine every 32 seconds, not every 17.86. Held to whole buckets, the toy's linear case
   was worth 6.4% more, with 7 barracks instead of 13.
9. **Production chained on one producer starts a whole number of buckets apart while its starts are whole**, since a
   start sits on a bucket's first second. For workers the grid answers it since 2026-11-19 (decision 2): on 2-second
   buckets an SCV started every 14 seconds, and every 16 on 4-second ones, against 12.14 in game, which the toy's
   12-second SCV hid; on buckets of 34 steps it starts every 272 steps. What is left is every build no bucket
   divides, up to a bucket longer each: a marine's 400 steps planned as 408, 2%, where 2-second buckets had 0.8%, and
   a marauder's or a supply depot's 480 as 510, 6%, where they had 2.7%. Only buckets of 16 steps divide every build,
   at 2.5 times the whole variables. Starts queued behind the build ahead would answer the rest, and are not to be
   built (the owner, 2026-11-19): what exact chains could add was measured by a script outside the package, a grid of
   16-step buckets until 4,352 steps, on which every chain is exact while its starts are whole, against one of
   34-step buckets of the same shape. The reward of `replans` was worth 0.07% more on it from 6:00 at a half-life of
   240 s and 0.05% undiscounted, both proven, and at least 0.03% more from a game's start, unproven after two
   minutes, the finer buckets' every other gain included. Against that, a queued start is done by its place in the
   queue and not by its bucket, so each item a producer makes would need a count done by each bucket, whole and so
   more integers, or fluid and so 1.9 marines done; and a barracks queues marines and marauders together, which sums
   cannot order. Per-item bucket lengths were weighed too: grids of other lengths merge to nearly 16-step buckets,
   and each item starting only on a doubling of 34 steps of its own, a share of its build time, left 18 to 66% fewer
   whole variables free, proved the reward of `replans` no sooner, and halved the wide reward's proof for plans worth
   0.7 to 1.4% less, the one grid's plan the better after 3 s in all eight cases.
10. **Workers per base** (the owner, review of #239: to come back to). The planner pools workers over every field
    and refinery, so a new SCV mines from when it is done, even one that has to walk to another base, about 10
    minerals too many each, and a new base's fields count from when its command center is done. Separate workers per
    base, with transfers, would roughly double the reference program's 1388 variables and add some 125 integers to its
    419, whether each future base stands yet, with the solver already short of optimal at 3 seconds; unmeasured. A
    cheaper middle way, a delay the `Economy` sets before each new SCV mines, was left for this discussion.

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
