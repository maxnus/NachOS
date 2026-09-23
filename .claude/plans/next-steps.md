# Next steps for nachOS

Written 2026-09-21 as a handover to the next agent, who will not have the previous agent's memory. It holds where
the work stands, what the repo owner has already decided, and the next steps in order, with what each depends on.
The working agreements (how to pace, commit, push and review) are in `.claude/CLAUDE.md`, which Claude Code loads
by itself.

Keep this file current: when a step is done, say so here in the same pull request, and cut what no longer helps.

## Where things stand

- nachOS `main` is at `c0bceb2` (PR #59). `uv run pytest` passes 1477 tests; `uv run pytest -m integration` passes
  19 against a real game (run 2026-09-21).
- **M4 slice 4, orders, is done**: PRs #42, #43 and #45 swept how the game takes orders, cancels and production;
  #44 is the order machinery (`api.orders`); #46 added `Cost`, `AbilityData.cost`, `AbilityData.cancelled_by` and
  `OrderState.LOST`. User docs: `docs/orders.md`. What the game was seen to do: `docs/game-behavior.md`, section
  "Abilities and orders".
- PRs #47 and #53 to #59 were housekeeping after a code review, mostly renames: `api.events`, `api.orders`,
  `api.orders.issued_to`, `ActionFailure` / `api.action_failures` / `order.failure`, `order.action_result`,
  `AbilityData.order_behavior`, `UpgradeData.upgrade_type`, `Unit.cloak_state`, `Units.closest_n_to`,
  `launch.MapFile`, `PlaybackTransport`, `_from_proto`.
- The consuming bot is AvocaDOS ([github.com/maxnus/AvocaDOS](https://github.com/maxnus/AvocaDOS), branch
  `main`). Its `docs/plans/nachOS-plan.md` is the milestone plan (M1 to M6) and records every nachOS PR; its
  `tests/test_nachos_parity.py` compares NachOS with python-sc2 over this repo's corpus and passes again as of
  AvocaDOS `51d0ed1`.

## Decided, so not to be proposed again

- **NachOS checks nothing an order needs** (#46). Minerals, vespene, supply, queue room and tech are the game's to
  judge and the bot's to budget. NachOS sends what it is given, and the order's state and `action_result` say what
  became of it. No budget, no refusal by NachOS itself, no `can_afford`, no `api.orders.cancel`. A built version
  with all of that was dropped as too complicated for what it bought: an advanced bot ranks its own spending anyway.
- **NachOS has no order priority of its own.** A unit takes the last order it is given in a turn. Handlers already
  run priority first, and a bot that wants a rank puts it in the order's `data` and reads `api.orders.issued_to`.
- **Production orders get no "replace".** A cancel frees no queue slot and no minerals in the same step, so a
  replace would leave a structure idle for a turn (#43).
- **`api.data` is static**: the game's tables, read once. Anything that depends on the game's progress is read
  from the state, never written into `api.data`.
- **A general research id costs its first level** (`ENGINEERING_BAY_RESEARCH_INFANTRY_WEAPONS` is 100/100): it runs
  the first level until that is done.
- **Reversed on 2026-09-21: NachOS will keep a queue per unit** (step 1 below). Until then it sent every order in
  the turn it was given and kept nothing across turns; that was a decision of 2026-09-19, which the owner has
  overturned.

## 1. A queue per unit, kept in NachOS

The owner's words (2026-09-21): *"we want a custom queue for every unit within nachos. There is never a point to
queue anything in game (exception: terran production with reactor) for a bot, so we move the queue system into the
library."* And earlier the same day: *"a worker walking to a location to build something but the build is not yet
sent to the game to avoid paying upfront. The intention to build is known to nachos however (i.e. unit type and
location) and as soon as the worker is close enough, they will start the build. Similar for the flying barracks +
build add-on."*

**Status: agreed in principle, not planned.** Start with a plan on a new branch off `origin/main`, and settle it
with the owner (Claude Code's plan mode) before writing code.

### Why: what the game does with an order it cannot carry out yet

All measured, in `docs/game-behavior.md` under "Abilities and orders" and "Units coming, changing and going":

- **A build is charged when ordered**, not when the builder arrives: a depot ordered 35 away took its 100 minerals
  by the next observation. A build queued behind a move is charged when given too, while the worker is still on
  its first leg.
- **A flying barracks given an add-on** needs a point (where to land), queues `BARRACKS_LAND` and the build, and is
  charged at once: the reactor's 50/50 was gone 430 steps before it landed. Given a point whose add-on spot is
  blocked, it answers `CantFindPlacementLocation`. A factory and a starport were not tried.
- **A train or research queued behind what a structure is making** is paid from the step it is ordered. With the
  supply cap full, a train is taken and charged, then sits at no progress as long as the cap stays full.
- **Refused now, allowed later**: a morph or add-on on a busy structure is `NotSupported`; a structure researching
  is offered no research at all; a warp gate and a larva keep no queue; a spell queued behind a move is dropped
  silently if the energy is gone by then.

### Cases, as the previous agent grouped them for the owner

1. **Charged when given, carried out later (the money case)**: a worker walking to build; a build behind any number
   of moves (scouting worker, proxy); a flying barracks, factory or starport and its add-on; a train or research
   queued behind what a structure is making (give the next item when a slot frees; on a reactor, two slots); a train
   with the supply cap full.
2. **Refused now, allowed later (the timing case)**: an orbital as soon as the SCV being trained finishes; +2 as soon
   as +1 finishes; a warp-in when the gate is ready; a larva morph when a larva is free; a spell on arrival; something
   whose tech requirement is still going up.
3. **Sequences the game may not queue at all (not measured)**: a flying command center that lands at an expansion
   and then becomes an orbital or planetary fortress; a lifted barracks that lands and then trains.

### What the plan has to settle

- **How it looks to a bot.** What `queued=True` means now (behind NachOS's queue, not the game's); how a bot adds,
  reads, reorders and withdraws items; what an `Order` in NachOS's queue reads as (a new state before `SENT`?); and
  the one exception, a structure with a reactor, where the game's own second slot is wanted.
- **What an unqueued order does to the queue**: presumably replaces it, as the game does.
- **When an item goes out.** Conditions on position and on the unit's state ("close enough", "landed", "idle", "gate
  ready", "larva free") fit the rule that NachOS checks nothing an order needs. Waiting on minerals, supply or tech
  would bring those checks back; the previous agent recommended leaving them to the bot and sending the item
  anyway, letting the game refuse it. The owner has not ruled on this yet: ask.
- **What ends a queue**: the unit dying (`LOST`), morphing, being taken over; an item refused or failed (does the
  rest go on?); observation lag, since an order's effect can show up an observation late.
- **What stays in the game's queue.** Speed mining needs the game's own queue so the next leg starts on the exact
  step; a queue held by the library advances only at a turn boundary. Micro re-decides every step on purpose, one
  order per unit. So the game's queue has to stay reachable for those.
- **What it rewrites**: #44's machinery (`orders/_order_book.py`, `_order.py`, `_order_state.py`), and
  `docs/orders.md`, where `queued=True`, the reactor example and the cancel paragraph change: taking back an item
  NachOS still holds becomes a plain withdraw, and only what the game already has needs a real cancel
  (`AbilityData.cancelled_by`).

### Measurements it needs (`tools/sweep_orders.py`, findings into `docs/game-behavior.md`)

- **"Close enough" for a build**: send the build at several distances and record the steps the worker loses. Too
  late and it stops and waits; too early and the minerals are spent while it walks.
- Whether a drone and a probe are charged at the order as an SCV is.
- What happens to a build whose site is blocked before the worker arrives (expected: an error and no charge).
- A factory's and a starport's add-on while flying, assumed to behave as a barracks's.
- Whether a flying command center can be given land and then a morph, and what a lifted barracks does with land and
  then a train.

### Suggested first pull request

The three money cases whose state is plain to observe: the worker build (with its behind-a-move form), the flying
add-on, and the queued train or research. The timing cases follow the same pattern and can come after. Confirm the
scope with the owner.

## 2. The rest of M4

From AvocaDOS's `docs/plans/nachOS-plan.md`, section "M4 slices". Each slice is one nachOS PR; the owner chooses
when each starts.

5. **The derived reads**: workers, townhalls, mineral fields, geysers, this player's start location, counts in
   production, and `api.enemy.units` against `api.units.enemy`.
6. **Debug and chat**: typed debug commands, drawing, sending chat, `query_pathing` and leaving a game.
7. **Typed order methods on `OwnUnit`**, or the decision to defer them.
8. **The demo bot**, M4's exit gate: a small bot in this repo (build workers, expand, attack) that plays a full game
   against `Computer` using only NachOS, and doubles as the library's example for other developers.

## 3. Deferred: the exact level a general research runs now

`api.data.abilities[general].cost` is the first level's. For the second and third, a state-aware lookup, for
example `api.next_level(general) -> AbilityId | None`: the lowest level whose upgrade is not in `api.upgrades`,
from `TECH_TREE.upgrade_levels` and `ability_remaps` (about 15 lines and tests). Never by mutating `api.data`. Not
measured: what the game does with a general id while a level is still researching (expected: refused). The owner
said "leave it for later".

## 4. After each nachOS pull request merges

Record it in AvocaDOS's `docs/plans/nachOS-plan.md` on `main`: a paragraph under "M4 status" saying what it
settled and what AvocaDOS writes differently at M5. That needs an AvocaDOS checkout; its tests (`uv run pytest`,
`tests/test_nachos_parity.py` in particular) install nachOS from source. Commit there, and push only when the owner
says so.

## 5. Later, at M5 (AvocaDOS runs on NachOS)

In AvocaDOS's plan, section "M5 — The swap". Already known:

- AvocaDOS's own `api.event` and `api.order` become NachOS's `api.events` and `api.orders`; its `OrderManager` keeps
  the *highest*-priority order where NachOS keeps the *last*, and `has_order(unit)` is `api.orders.issued_to(unit)`.
- `phase=` becomes `priority=`, with the handlers that forget the dead at `HIGHEST`.
- The bot's `MEDIVACINCREASESPEEDBOOST`, `DUTCHMARAUDERSLOW` and `IMMORTALOVERLOAD` take their curated names;
  AvocaDOS's `tests/test_nachos_id_parity.py` fails on those three until then, as expected.
