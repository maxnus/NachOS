"""What a handler returns once it has nothing more to do in a game."""

from typing import NoReturn, final


@final
class Done:
    """Returned by a handler, the class itself, to be called no more this game. It is called again from the next.

    Any other return value is ignored.
    """

    def __new__(cls) -> NoReturn:
        raise TypeError("a handler returns `Done` itself, not an instance of it")
