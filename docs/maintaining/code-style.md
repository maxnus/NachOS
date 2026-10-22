# Code style

How code and docstrings here are named and written. Much of it is carried over from AvocaDOS, so that the two
codebases read alike. A new rule of style goes here, with an example from the code.

## Names

- **Name a class for what it is responsible for**, so the name says what it does. Every name is descriptive and
  accurate: a plain name a reader can predict beats a clever synonym.
- **A constant, a function or a variable is named for what it holds or returns**, so its name needs no comment:
  `_MIN_MINERAL_FIELDS_PER_EXPANSION`, never `_FEWEST`, and `ground_offset`, never `shift`. A comment that explains a
  name means the name is wrong. A lambda's parameter, which lives for one line beside what it iterates, may be short:
  `lambda exp: exp.center`.
- **Never a one-word name for a constant or a helper function**, in `tools/` too. A predicate reads as a clause
  where it is called: `_holds_a_tag_of(dead, unit)`, `_new_tag_is_same_unit(unit, display)`. A pull request renames a
  one-word name in the code it touches, not only in the code it adds.
- **A name that holds an instance of a NachOS class follows the class**: `order_book: OrderBook`, never `orders`. A
  constructor's parameter is named for what it takes: `static_placement_grid`, `present_units`.
- **A classmethod that turns a value of another kind into an instance of its class is named `from_` that value**:
  `Footprint.from_rows`, `GamePorts.from_start_port`, `Player._from_game`. One that builds a shape or a state names
  what it builds: `Footprint.square`, `Grid.zeros`. One that finds the instance at a position is named `at`:
  `Tile.at(point)`, the tile a point falls in. `of` names what selects part of a whole, `Units.of_type` and
  `AnyAreaEvent.of(area)`, or what reads something of a value, `_key_of`.
- **A leading underscore marks what a bot must not use**, not what another NachOS module must not call.

## Signatures

- **Keyword-only args**: use `*` in signatures liberally.
- **Type hints** on all parameters and return types.
- **A filter takes one value or a collection** (`UnitTypeId | Collection[UnitTypeId]`) and normalizes inside.

## Structure

- **One class per file** (except small data classes). File named after the class, snake-cased.
- **No circular imports.** Where two modules would import each other, move what both need into a module of its own.
  A `TYPE_CHECKING` guard around an import that would be circular is the last resort.
- **A value derived from a data object is a property on that object**, not a lookup its callers repeat.
- **Short functions**: extract a named helper rather than growing the caller.
- **Values that do not belong together are assigned on lines of their own**: `units = ...` and `reader = ...`, never
  `units, reader = ..., ...`. Several are assigned at once only as the parts of one thing: a value taken apart
  (`x, y = point`), a corner's two coordinates (`left, bottom = int(within.x), int(within.y)`), or a swap.
- **`__all__`** only where it earns its place — package `__init__.py` files that curate a public surface.
- **loguru**, not stdlib `logging`.
- **Errors**: a failure of the library's own raises a subclass of `NachOSError`, which also subclasses the
  built-in it is a case of, where one fits: `ConnectionClosedError` is a `ConnectionError`. Misuse, such as a bad
  argument, raises the built-in (`ValueError`, `TypeError`, `IndexError`). A dependency's exception is translated
  where it enters, with `raise ... from`, and never reaches the caller.

## Docstrings

- **Docstrings** on all public functions and classes, but without parameter/return sections.
- **A docstring states the contract and never argues for the design.** Delete any sentence that explains why the
  code is shaped this way (a name, a method rather than a property, where something lives); keep what a caller
  cannot infer (an edge case, a rounding rule, what it raises). An answer given in chat does not also go into the
  file; if it is worth keeping, it goes in the commit message. After a round of doc edits, list the multi-line
  docstrings with `uv run python tools/list_multiline_docstrings.py` and check each.

## Words

- **US spelling** everywhere in code, comments, docstrings and docs — `behavior`, `initialize`, `summarize`,
  `color`, `center`. Two exceptions: generated identifiers, where `ids/raw/` mirrors Blizzard's own names verbatim
  (`BuildinProgressNonCancellable`); those are data, never to be "corrected". And **"cancelled"**, which the code,
  the tests and the docs spell with two l's throughout.
- **One game loop is a step.** Above the protocol layer, time is counted in steps -- `Api.step`,
  `steps_per_turn`, `steps_to_seconds` -- and the bot's own cycle is a turn, which nothing counts. The protocol
  layer keeps Blizzard's `game_loop`, since the messages it hands back carry that field, and `Api.play` is the one
  place the two meet. Never write "frame" for either.
