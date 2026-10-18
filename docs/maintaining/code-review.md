# Code review checklist

Each of these came from a real bug found in review, mostly in code that looked correct and passed its tests.

- **Annotate as tightly as the value allows.** `Self` for type-preserving operations, exact tuple arity
  (`tuple[float, float]`, not `tuple[float, ...]`), fixed-length returns where the count is in the name, real
  protobuf types under `TYPE_CHECKING`.
- **No false IS-A.** Types of different shape must not inherit from each other — `Point3` is not a `Point2`, a
  `Rect` is not a point. Share behavior through a non-public base instead. A subtype claim that is not
  substitutable makes every downstream bug typecheck cleanly.
- **Never silently discard data.** An operation that cannot preserve a coordinate, a field, or a dimension must
  raise, with a message naming both operands and the explicit conversion. Padding and truncation hide bugs.
- **Numeric checks must accept numpy scalars.** Only `numpy.float64` subclasses `float`; `float32` and the
  integer types subclass neither. Use `numbers.Real`, ordered *after* the concrete types — the ABC check is
  roughly 3x slower, so the common path must not reach it.
- **A `tuple` subclass must define `__radd__` and `__rmul__`.** Otherwise `(1, 2) + point` inherits concatenation
  and `2 * point` inherits repetition, both returning a wrong answer with no error.
- **`__contains__` has no reflected form.** Return a bool, never `NotImplemented` — it is truthy, so
  `"banana" in rect` returns `True`.
- **Every exported name is a promise.** No public API without a caller or a test that shows why it exists, and no
  second spelling of an operation that already exists.
- **Measure before claiming.** Benchmark competing shapes rather than reasoning about them; grep for real call
  sites before calling something hot.
- **A name must not claim behavior the code lacks.** The most common defect found in review, and one tests never
  catch: `rescale` was a plain `lerp`, and `Area.size` meant area for two shapes and a point count for the third.
  Read the body, then ask what the name promised.
- **Test invariants across implementations, not one at a time.** `closest_point_to` returned a tile center from
  `TileSet` and a boundary point from `Tile` and `Rectangle`, and every per-class test passed. Parametrize one
  probe over every implementation of the interface.
- **A base class without `__slots__` gives every subclass a `__dict__`**, which makes their own slots inert and
  lets attributes be set on a frozen value. `functools.cached_property` needs that dict, so a class that wants one
  declares no slots, and a `cached_property` cannot override an abstract `property`.
- **Anything that picks or orders the elements of a set must sort them first.** Two equal `frozenset`s can
  iterate in different orders depending on how each was built, which once made a seeded game unreproducible.
- **An unset proto2 enum field reads as its first declared value, not zero.** `ResponseJoinGame.error` unset is
  `MissingParticipation`, which is truthy, so reading it blind refuses every successful join. Gate optional enum
  reads with `HasField`, spelled out at the call site: the stubs type its argument as a `Literal` of field names,
  which a helper taking `field: str` defeats.
- **`isinstance` against an ABC costs ~6x a plain class when it misses**, so a type switch over `Area`
  implementations pays that for every branch it rejects. Prefer a virtual method, which is also open to new shapes.
- **Reading a field out of a protobuf message costs over ten times a slot read.** A value read many times a turn
  is copied out once, when the observation arrives.
