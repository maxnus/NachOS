# Code style

How code here is named and written, carried over from AvocaDOS so that the two codebases read alike.

## Names

- **Name a class for what it is responsible for**, so the name says what it does. Every name is descriptive and
  accurate: a plain name a reader can predict beats a clever synonym.
- **A constant, a function or a variable is named for what it holds or returns**, so its name needs no comment.
- **A leading underscore marks what a bot must not use**, not what another NachOS module must not call.

## Signatures

- **Keyword-only args**: use `*` in signatures liberally.
- **Type hints** on all parameters and return types.
- **A filter takes one value or a collection** (`UnitTypeId | Collection[UnitTypeId]`) and normalizes inside.
- **`TYPE_CHECKING` guard** for imports that would otherwise be circular.

## Structure

- **One class per file** (except small data classes). File named after the class, snake-cased.
- **A value derived from a data object is a property on that object**, not a lookup its callers repeat.
- **Short functions**: extract a named helper rather than growing the caller.
- **Docstrings** on all public functions and classes, but without parameter/return sections.
- **`__all__`** only where it earns its place — package `__init__.py` files that curate a public surface.
- **loguru**, not stdlib `logging`.
- **Errors**: a failure of the library's own raises a subclass of `NachOSError`, which also subclasses the
  built-in it is a case of, where one fits: `ConnectionClosedError` is a `ConnectionError`. Misuse, such as a bad
  argument, raises the built-in (`ValueError`, `TypeError`, `IndexError`). A dependency's exception is translated
  where it enters, with `raise ... from`, and never reaches the caller.

## Words

- **US spelling** everywhere in code, comments, docstrings and docs — `behavior`, `initialize`, `summarize`,
  `color`, `center`. Two exceptions: generated identifiers, where `ids/raw/` mirrors Blizzard's own names verbatim
  (`BuildinProgressNonCancellable`); those are data, never to be "corrected". And **"cancelled"**, which the code,
  the tests and the docs spell with two l's throughout.
- **One game loop is a step.** Above the protocol layer, time is counted in steps -- `Api.step`,
  `steps_per_turn`, `steps_to_seconds` -- and the bot's own cycle is a turn, which nothing counts. The protocol
  layer keeps Blizzard's `game_loop`, since the messages it hands back carry that field, and `Api.play` is the one
  place the two meet. Never write "frame" for either.
