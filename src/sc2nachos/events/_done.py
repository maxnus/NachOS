"""What a handler returns when it is done."""

from typing import NoReturn, final


@final
class Done:
    """A handler returns this class itself to be called no more.

    Any other return value is ignored.
    """

    def __new__(cls) -> NoReturn:
        raise TypeError("a handler returns `Done` itself, not an instance of it")
