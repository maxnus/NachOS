"""What the planner raises."""

from sc2nachos import NachOSError


class NoPlanFoundError(NachOSError, RuntimeError):
    """The solver found no plan within its limits."""
