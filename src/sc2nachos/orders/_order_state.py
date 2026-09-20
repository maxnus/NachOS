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
    """Every unit it went out for died before it was done, so nothing came of it. The game reports a producer dying
    and nothing more, and a unit that is gone is carrying nothing out, so this is what tells a marine that was
    trained from a barracks killed half way through it. An order whose unit is used up carrying it out -- a larva
    becoming a drone, a drone becoming a hatchery -- is `DONE` instead."""
    CANCELLED = "cancelled"
    """Taken back with `api.order.cancel`, which the game answered `SUCCESS`: what it was making is off the
    structure, and the game gives back its share of what it charged in a later observation."""
    REFUSED = "refused"
    """Answered something other than `SUCCESS`, or never sent because the turn could not pay for it: the verdict
    says what, and is what the game would have answered either way."""
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
        OrderState.CANCELLED,
        OrderState.DONE,
        OrderState.LOST,
        OrderState.REFUSED,
        OrderState.DROPPED,
        OrderState.FAILED,
        OrderState.OVERRIDDEN,
        OrderState.WITHDRAWN,
    }
)
