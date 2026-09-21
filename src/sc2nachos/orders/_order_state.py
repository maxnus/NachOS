"""How far an order the bot gave has got."""

from enum import Enum


class OrderState(Enum):
    """What has become of an order. Each of these is something the game was seen to do (tool `sweep_orders`)."""

    GIVEN = "given"
    """Given this turn, and nothing sent yet."""
    SENT = "sent"
    """Sent, and answered `SUCCESS`, with no observation since to say what came of it."""
    RUNNING = "running"
    """The game reported carrying it out, or the unit was already doing it and it was not sent again."""
    DONE = "done"
    """No unit it was given to is carrying it out any more."""
    LOST = "lost"
    """Every unit seen carrying it out died before it was done, so nothing came of it; where none was ever seen,
    every unit it went out for. The game reports a producer dying and nothing more, and a dead unit carries nothing
    out, so this is what tells a barracks killed half way through a marine from one that finished it. Of three
    barracks given one train, it is the one that took it. An ability whose effect is its unit's death, a baneling
    exploding, reads `LOST` too, and so does one whose unit finished it and died before the next observation, which
    cannot be told apart from one killed at it. A larva's order is `DONE` instead: its egg is reported dead as what it
    makes hatches, and an egg killed first reads the same (in game)."""
    REFUSED = "refused"
    """Sent, and answered something other than `SUCCESS`: `action_result` says what."""
    DROPPED = "dropped"
    """Answered `SUCCESS` and never carried out, which the game does silently where an order no longer fits by the
    time it steps: a sixth marine, an order there are no minerals left for."""
    FAILED = "failed"
    """Carried out and then given up on, which an `ActionFailure` names: a builder's site taken meanwhile."""
    OVERRIDDEN = "overridden"
    """A later order took every unit this one was given to: one of the same turn, before this was sent, or one of a
    later turn, which the game carried out in its place. `action_result` says which, being `None` for an order never
    sent."""
    WITHDRAWN = "withdrawn"
    """Taken back by the bot. One already sent is only forgotten: the unit goes on with it."""

    @property
    def is_final(self) -> bool:
        """Whether nothing more can become of it, so NachOS has stopped following it."""
        return self in _FINAL


_FINAL = frozenset(
    {
        OrderState.DONE,
        OrderState.LOST,
        OrderState.REFUSED,
        OrderState.DROPPED,
        OrderState.FAILED,
        OrderState.OVERRIDDEN,
        OrderState.WITHDRAWN,
    }
)
