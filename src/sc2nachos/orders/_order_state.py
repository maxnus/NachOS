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
    REFUSED = "refused"
    """Sent, and answered something other than `SUCCESS`: the verdict says what."""
    DROPPED = "dropped"
    """Answered `SUCCESS` and never carried out, which the game does silently where an order no longer fits by the
    time it steps: a sixth marine, an order there are no minerals left for."""
    FAILED = "failed"
    """Carried out and then given up on, which an action error names: a builder's site taken meanwhile."""
    OVERRIDDEN = "overridden"
    """A later order took every unit this one was given to: one of the same turn, before this was sent, or one of a
    later turn, which the game carried out in its place. `verdict` says which, being `None` for an order never
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
        OrderState.REFUSED,
        OrderState.DROPPED,
        OrderState.FAILED,
        OrderState.OVERRIDDEN,
        OrderState.WITHDRAWN,
    }
)
