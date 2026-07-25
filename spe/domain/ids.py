"""Injectable identifier generators.

Identifiers are produced through an :class:`IdGenerator` so tests can supply a
deterministic, monotonically increasing sequence. Production uses UUIDv4.
"""

from __future__ import annotations

import uuid
from itertools import count
from typing import Protocol


class IdGenerator(Protocol):
    """Produces opaque, globally unique string identifiers."""

    def new_id(self) -> str:
        """Return a fresh identifier."""
        ...


class UuidGenerator:
    """Production generator backed by :func:`uuid.uuid4`."""

    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequentialIdGenerator:
    """Deterministic generator for tests.

    Emits ``{prefix}-{n}`` with a monotonically increasing counter so that
    generated ids are stable and human readable inside assertions and traces.
    """

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = count(1)

    def new_id(self) -> str:
        return f"{self._prefix}-{next(self._counter)}"
