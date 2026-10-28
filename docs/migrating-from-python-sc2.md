# Migrating from python-sc2

For bots moving over from [python-sc2](https://github.com/BurnySc2/python-sc2), the `burnysc2` package imported as
`sc2`. NachOS is not a drop-in replacement. This page shows the same bot in both, the main changes, and where a
translation most often goes wrong. [python-sc2 and NachOS](reference/python-sc2.md) has every python-sc2 name a bot is
likely to use, and every difference, for looking one up.

## The same bot in both

A worker rush that keeps training SCVs, in python-sc2:

```python
from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.main import run_game
from sc2.player import Bot, Computer


class WorkerRush(BotAI):
    async def on_step(self, iteration: int) -> None:
        if iteration == 0:
            for worker in self.workers:
                worker.attack(self.enemy_start_locations[0])
        for townhall in self.townhalls.ready.idle:
            if self.can_afford(UnitTypeId.SCV):
                townhall.train(UnitTypeId.SCV)


run_game(
    maps.get("PylonAIE_v4"), [Bot(Race.Terran, WorkerRush()), Computer(Race.Zerg, Difficulty.Easy)], realtime=False
)
```

and in NachOS:

```python
from sc2nachos import Api, Computer, Difficulty, Race, run_local
from sc2nachos.events import TurnEvent
from sc2nachos.ids import AbilityId

api = Api()


@api.on(TurnEvent, once=True)
def rush(event: TurnEvent) -> None:
    api.orders.issue(api.units.own.workers, AbilityId.ATTACK, target=api.map.opponent_start_locations[0])


@api.on(TurnEvent)
def train_scvs(event: TurnEvent) -> None:
    cost = api.data.abilities[AbilityId.COMMAND_CENTER_TRAIN_SCV].cost
    for townhall in api.units.own.townhalls.complete.idle:
        pending = api.orders.pending_cost
        budget = api.resources - pending.resources
        if budget.covers(cost.resources) and api.supply.left - pending.supply >= cost.supply:
            api.orders.issue(townhall, AbilityId.COMMAND_CENTER_TRAIN_SCV)


run_local("PylonAIE_v4", api, Computer(Race.ZERG, Difficulty.EASY), race=Race.TERRAN, steps_per_turn=4)
```

- **No subclass and no `async`.** Handlers subscribe to an `Api`, which can be made at import time, anywhere in the bot.
- **`iteration == 0` is `once=True`.** There is no iteration: `event.time` is when the event came, and `min_interval`
  counts game time.
- **An order goes to the units given, all at once**, and is sent when the turn's handlers have run.
- **Nothing is subtracted as you order.** `can_afford` is the bot's own: what the turn's orders will cost is
  `api.orders.pending_cost`.
- **`steps_per_turn=4` matches python-sc2's `game_step`.** NachOS takes a turn every step unless told otherwise, and its
  `Computer()` defaults to very hard.

## The main changes

- **A bot subscribes handlers to an `Api` rather than subclassing `BotAI`**, and nothing is `async`. `on_step` and the
  other hooks become events, and a handler selects the ones it wants ([events](events.md),
  [in detail](reference/python-sc2.md#events)).
- **Game time is counted in steps**, the game loop, never in iterations. A turn is one step unless `steps_per_turn`
  says otherwise ([in detail](reference/python-sc2.md#running-a-game)). Every moment and length of game time is a
  `GameTime`, which compares only with another: `self.time > 300` is `api.time > GameTime.from_seconds(300)`, and
  `self.time` alone `api.time.seconds` ([in detail](reference/python-sc2.md#time)).
- **A unit is one object under one id for the whole game.** Out of sight it is unlisted, not gone, and only death
  ends it. A tag is only the game's current handle for it ([in detail](reference/python-sc2.md#units)).
- **Your own units are `OwnUnit`s**, the only ones with orders, cargo and weapon cooldown. A unit's type can be a type
  parameter: `units.own.of_type(UnitType.Marine)` is typed as your marines ([in detail](reference/python-sc2.md#units)).
- **Ids use the names players use**, `CONCUSSIVE_SHELLS` for `PUNISHERGRENADES`, cover only what a melee game needs,
  and are ints ([in detail](reference/python-sc2.md#ids)).
- **Points compare exactly and never mix 2D with 3D**, and grids are indexed `[x, y]`
  ([in detail](reference/python-sc2.md#points-areas-and-grids)).
- **A unit's weapons, speed and armor include its owner's upgrades**, the enemy's too, and speeds are per second of
  Faster speed ([in detail](reference/python-sc2.md#units)).
- **The tables are keyed by id, and the relations between them were swept in game**, replacing python-sc2's
  hand-written dicts ([in detail](reference/python-sc2.md#the-tables)).
- **Orders are buffered and sent once a turn** through `api.orders`, and what the game refuses is listed in
  `api.action_failures` ([orders](orders.md), [in detail](reference/python-sc2.md#orders)).
- **Drawing, cheats and debug commands go through `api.debug`**, and chat through `api.chat`
  ([debugging](debugging.md), [in detail](reference/python-sc2.md#debug-chat-the-camera-and-leaving)).
- **One script plays the ladder's games and local ones**: `run_from_command_line` reads the ladder's arguments, which a
  python-sc2 bot's `run.py` reads itself ([playing on the ladder](ladder.md)).

## Where a translation goes wrong

- **A unit takes the last order a turn gives it.** A second train given to a structure replaces the first; a reactor's
  second marine needs `queued=True` ([orders](orders.md#one-order-a-unit-a-turn)).
- **A larva's morph is given to a larva**, a drone or an overlord say, not to the hatchery, which refuses it
  ([in detail](reference/python-sc2.md#orders)).
- **A tag is not a unit.** A structure gets a new tag each time it leaves sight; keep `unit.id`, or the unit itself,
  and before ordering a unit you kept, check `unit.is_orderable`, false while it is in a transport or under a neural
  parasite ([in detail](reference/python-sc2.md#units)).
- **`api.units` is every unit**, of every side; python-sc2's `bot.units` is this player's non-structures, which is
  `api.units.own.non_structures` ([in detail](reference/python-sc2.md#units)).
- **Out of sight is unlisted, not gone.** `api.units` is what the observation lists, and `api.known_units` adds what
  is remembered ([in detail](reference/python-sc2.md#units)).
- **Only your own units have orders**, cargo, rallies and weapon cooldown; the game reports them for nobody else's
  ([in detail](reference/python-sc2.md#units)).
- **Speeds are per second of Faster speed already, and include upgrades**: drop python-sc2's `1.4 *`
  ([in detail](reference/python-sc2.md#units)).
- **A dps is per second of Faster speed too**, where python-sc2's `ground_dps` is per Normal second, 1.4 times lower
  ([in detail](reference/python-sc2.md#units)).
- **2D and 3D points do not mix**, so convert with `.ground` or `.with_height(z)`; and python-sc2's `rounded` floors,
  which is `Tile.at(p)` here ([in detail](reference/python-sc2.md#points-areas-and-grids)).
- **Grids are indexed `[x, y]`** and cover the playable area alone ([in detail](reference/python-sc2.md#the-map)).
- **The names changed, not the numbers**: `UnitTypeId(old.value)` translates an id
  ([in detail](reference/python-sc2.md#ids)).
- **Half a supply is kept**: the game rounds a zergling's half down, and NachOS adds it back, so a zergling made at 14
  supply reads 14.5 ([in detail](reference/python-sc2.md#the-state)).
- **Subscriptions outlive a game.** Objects a bot makes for each game unsubscribe the old ones, or both go on handling
  events ([in detail](reference/python-sc2.md#events)).
- **`api.chat` and `api.leave_game()` act at the end of the turn**, and a goodbye sent in the turn that leaves never
  arrives ([in detail](reference/python-sc2.md#debug-chat-the-camera-and-leaving)).
- **A pathing distance with no path is `math.inf`**, not `None` ([in detail](reference/python-sc2.md#the-map)).
