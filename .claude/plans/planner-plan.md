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
2. **A grid that widens with time: 2-second buckets doubling every 45 seconds** or so, to 32 seconds by ten minutes,
   62 buckets in all. Ten-second buckets undertrained workers, an SCV every 20 to 30 seconds instead of every 12, and
   undervalued the plan by 5%; 2 seconds is within 0.01% of 1 second at half the solve time. A doubling grid keeps
   the relative timing error constant along the plan. Durations are never rounded: a build enters the producer
   constraints in exact seconds split across the buckets it overlaps; only a start sits on a bucket's first second
   and a completion on the next edge.
3. **Whole numbers where an item is lumpy, fluid flows where it is not**, for longer the more it costs (the owner,
   phase 1): an item's starts are integer from the plan's start for 60 seconds for each 100 minerals and vespene, at
   least 30, and fluid after: a command center to 4:00, a factory or a starport to 2:30, a barracks or an orbital
   command to 1:30, a depot to 1:00, an SCV or a marine to 0:30. Upgrades stay integer over any horizon. A bot sets
   its own for any unit type, `Planner(whole_until=...)` taking a function of the unit type that returns `None` for
   the default. The rule: an item stays whole where its cost exceeds a bucket's income and it is made once a bucket
   at most. Fluid far-term starts make the far term the value function of the near term, computed by the LP at each
   node instead of learned; the near-term plan did not change when the window moved from 1:30 to 10:00. Depots kept
   whole for minutes stall the solver: 22 near-identical integer starts are symmetric branches. Phase 1 first kept
   townhalls, production and tech structures whole over the whole horizon and the rest for 90 seconds, which left the
   reference configuration 7.6 to 12% from its bound after 3 seconds and 7 to 11% after 10; by cost it is proven
   optimal in 8 to 10 seconds (Open 7).
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
10. **No caps on production structures.** Their number is bounded by the economics; the physical bounds stay: the
    free expansions the bot passes, none by default, two refineries a base, one add-on a barracks, one of each
    upgrade, supply 200, and room to build near a base once the map supplies it.
11. **Every input is visible and the bot's to change** (the owner, review of #239). What the game shows is a public
    `GameState`, which `Planner.game_state` reads and `plan_now(state=...)` takes back changed, a base about to fall
    left out, say; it holds only what the game shows, never an assumption. What the bot assumes is an `Economy`
    handed to each `plan_now`: what each worker at a field or a refinery brings in a second, a list from the first
    worker on, the MULEs' income, a new base's fields and geysers, and the walks; and `loss_rates` and
    `free_expansions` beside it. The constructor keeps only how to solve: the grid, the limits and the windows.
    Workers stay pooled over every field and refinery (Open 10).

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
  refineries.
- `_grid.py`: bucket edges from segments. Each item's integrality window comes from its cost, or from the bot.
- `_game_state.py`: the state as plain values, `GameState`, read from the api when `plan_now` asks: resources, supply,
  the alive count of each type, each item in production with its time left, each structure an SCV builds and whether
  one walks there first, upgrades done and researching, and the fields, refineries and free geysers at the bot's
  bases. A bot may change it and hand it back.
- `_economy.py`: `Economy`, what the bot assumes of its economy (decision 11), with defaults measured in game.
- `_reward.py`: the reward as data the matrix builder reads, and the extension's public interface: per-type weights
  or concave segments, coverage (effectiveness, enemy counts per bucket, values), floors, the discount half-life, the
  terminal value, the hysteresis bonus and the plan it favours. The bot builds it; the planner never guesses it.
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

1. **The extension, for Terran, synchronous**, in NachOS, built in phase 1's pull request but for its exit's 3
   seconds (Open 7). Model from `api.data`, grid, reward, matrix, solve, plan,
   `plan_now()`. Tests on tiny instances with known optimal plans (the first depot at 8 seconds from 50 minerals and
   twelve SCVs; one marine at a time from one barracks; the command center at 48 seconds after uninterrupted SCVs),
   on the toy's cases as `tools/benchmark_planner.py`, and on made-up games through `sc2nachos.testing` for the
   snapshot. A section under "NachOS's own" in `docs/extensions.md`, this plan and the toy moved in, and
   `next-steps.md` pointing here. Exit: the toy's results reproduced from the
   game's data, and the reference configuration, doubling grid with the windows above, solved within 3 seconds on a
   developer machine.
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
3. **The item set.** The real model has more types than the toy's 14, and integers scale with them. Prune to what
   the reward reaches, and measure on the full Terran tree in phase 1.
4. **Build space.** A bound on structures per base from the map, as the one physical limit the model lacks;
   `api.placement` may answer it.
5. **Replanning defaults.** The interval, the budget and the hysteresis bonus are parameters; their defaults come
   out of phase 3's games.
6. **The discount against the solver.** A discount made a 7-minute solve faster and a 10-minute one slower; settle
   it on the real model.
7. **Phase 1's exit: the reference configuration is not solved within 3 seconds** (measured in phase 1's pull
   request). With decision 3's windows by cost, the concave reward is proven optimal in 8 to 10 s at every half-life,
   and the plan found after 3 s is worth 0.5 to 0.8% less than the optimal one, the solver's bound still 2.0 to 2.5%
   above it; the toy's linear reward is optimal in 8 s, but 6.5% short after 3. With phase 1's first windows,
   townhalls, production and tech structures whole over the horizon, the concave reward was 7.6% from its bound after
   3 s at a half-life of 240 s and 12% undiscounted, still 7 to 11% after 10 s: the root relaxation of the whole
   structures is what stayed loose, and a tighter big-M did not help. Left for the owner: the incumbent at 3 s, or
   phase 3's warm start. With no cap on free expansions, a bucket's command centers are bounded by 100, and the
   prerequisite rows' big-M of a morph and a refinery with them, to about 101 and 202 at a game's start, where three
   free expansions gave 4 and 8; the reference was no slower, optimal in 7.0 to 11.0 s. A valid tighter bound, if the
   3 seconds call for one: the command centers the horizon's resources can pay for.
8. **The count-form cut holds only where a bucket's starts are whole** (phase 1, for the owner to confirm). The toy
   applied it in every bucket shorter than every build in it, fluid ones too, where it is no valid cut: in 16-second
   buckets a barracks made a marine every 32 seconds, not every 17.86. Held to whole buckets, the toy's linear case
   was worth 6.4% more, with 7 barracks instead of 13.
9. **Production chained on one producer starts a whole number of buckets apart while its starts are whole**, since a
   start sits on a bucket's first second: an SCV every 14 seconds on 2-second buckets and every 16 on 4-second ones,
   against 12.14 in game. The toy's 12-second SCV hid it. Phase 6's simulator, or starts queued behind the build ahead,
   would answer it.
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
