# Debugging

A bot draws on the game's screen, turns cheats on and makes units through `api.debug`. What it asks for in a turn goes
out after the turn's orders, in one debug request. The game takes it from a client that joined the game as from the one
that created it (in game); what a ladder's own proxy lets through, NachOS cannot see, and a debug request the game
refuses raises `ProtocolError` out of `api.play`, ending the bot's game, so a bot draws only where it is let. The Linux
client renders nothing, so a drawing shows only in the game's window on Windows or macOS (stated).

## Drawing

```python
from sc2nachos import GameTime
from sc2nachos.debug import Color, DebugBox, DebugGroup, DebugLine, DebugMark, DebugText, Marker


@api.on(GameStartEvent)
def set_up(event: GameStartEvent) -> None:
    api.debug.refresh_interval = GameTime(steps=4)
    api.debug.default_color = Color.YELLOW
    for expansion in api.expansions:
        api.debug.draw(expansion.center, DebugMark(Marker.HEXAGON, radius=6), shown_for=None)


@api.on(TurnEvent)
def label(event: TurnEvent) -> None:
    for marine in api.units.own.of_type(UnitType.Marine):
        api.debug.draw(marine, DebugBox(1, 1), DebugText(f"{marine.health:.0f}", offset=(0, 0, 1.5)))
    api.debug.draw_text_on_screen(event.time.to_string(), at=(0.01, 0.01))
```

`api.debug` belongs to the game being played, and raises `NotPlayingError` before the first. `api.debug.draw(at,
*shapes)` draws shapes at a point or a unit as one `Drawing`, which it returns:

- **What.** The shapes are values, made once and drawn anywhere: `DebugText`, a `DebugBox` centered on its place and
  standing on it, a `DebugSphere`, a `DebugMark` (a cross, a plus, a star, or a triangle, a square, a pentagon or a
  hexagon with a level base), and a `DebugLine` to another point or unit. A `DebugGroup` of shapes is a shape too. Text
  on the screen is `draw_text_on_screen`, placed from 0 to 1 across and down, whatever the window's size.
- **Where.** A point of two coordinates is drawn on the ground, and one of three at its own height. A unit is drawn
  where it stands, and a drawing at a unit follows it, as does a line to a unit. Out of sight, the unit is drawn where
  it was last seen, and the drawing ends once the unit is dead. Each shape and each group takes an `offset` from where
  it is drawn, and a line a `to_offset` from where it ends.
- **How long.** A drawing is shown for the `shown_for` it is given, a whole number of steps, one by default, which is
  the turn it is made in. With `shown_for=None` it lasts until `drawing.remove()`.
- **How often.** `api.debug.refresh_interval`, a step unless set, is the least time between two refreshes of the
  drawing, a whole number of steps; meanwhile the screen keeps the last one sent. A refresh whose drawing is what the
  screen already shows sends nothing, so a drawing that never changes costs a request at the first refresh after it is
  made and one at the first after it ends. A cheat or a command goes out at once, with the drawing as it stands.
- **Colors** are a `Color`, such as `Color.RED`, or any red, green and blue, each from 0 to 255. A shape with no color
  of its own takes its group's, then the drawing's, then `api.debug.default_color`, white unless set; text with no size
  is `api.debug.default_text_size` pixels high, 8 unless set.

Each game starts afresh, so a `GameStartEvent` handler sets `refresh_interval` and the defaults for every game.

## Cheats

```python
api.debug.turn_on(Cheat.SHOW_MAP)
if Cheat.GOD not in api.debug.cheats:
    api.debug.turn_on(Cheat.GOD)
api.debug.give_resources()
```

A cheat is on for the whole game. `api.debug.cheats` is what this player has turned on, and turning one on that is on
already, or off that is off, sends nothing. In a game of two the other player can switch a cheat too, which this does
not see. [cheats.md](reference/game-behavior/cheats.md) says what several do to what the game reports; read it before
a test turns one on.

Three hand something out each time they are called: `give_minerals()` 5000 minerals, `give_resources()` 5000 minerals
and 5000 vespene, and `give_upgrades()` the next level of every upgrade of every race (in game).

## Commands

```python
api.debug.create_units(UnitTypeId.MARINE, at=natural.center, count=4)
api.debug.create_units(UnitTypeId.ZERGLING, at=natural.center, count=8, owner=Alliance.ENEMY)
api.debug.set_vitals(api.units.own.of_type(UnitType.Marine), life=10)
api.debug.kill(api.units.enemy.of_type(UnitType.Zergling))
api.debug.declare_victory()
```

- **`create_units`** makes units for this player, the one player it plays against, or the map, whose units read owner
  16. The game can make them a few steps late, beside the point, or not at all: no structure on ground it cannot stand
  on, and a larva or a creep tumor only on creep ([game behavior](reference/game-behavior/cheats.md)).
- **`set_vitals`** sets life, energy or shields, and raises `ValueError` for 0, which the game sets to the most a unit
  can have instead. `kill` takes a unit's life.
- **`declare_victory()` and `surrender()`** end the game by the next step, its result reported as any ending's.
  `api.leave_game()` concedes too, but nothing is reported after it, and `play` returns `Result.DEFEAT`.
- Hanging or crashing the game client and setting a curriculum map's score stay raw protocol messages, through
  `api.client.debug`.
