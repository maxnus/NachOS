"""The metaclass that makes every event class a frozen, slotted dataclass."""

from dataclasses import Field, dataclass, field
from typing import Any, dataclass_transform


@dataclass_transform(frozen_default=True, field_specifiers=(Field, field))
class _EventMeta(type):
    """The metaclass of every event class. It makes each a frozen, slotted dataclass of the fields it declares."""

    def __new__(mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any], /, **kwargs: Any) -> type:
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        # `slots=True` recreates the class through this metaclass, with its fields already in the namespace.
        if "__dataclass_fields__" in namespace:
            return cls
        return dataclass(frozen=True, slots=True)(cls)
