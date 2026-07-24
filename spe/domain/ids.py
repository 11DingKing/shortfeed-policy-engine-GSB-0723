"""Injectable identifier generators.

Production code uses :class:`UUID4IdGenerator`; tests and replay code can use
:class:`SequentialIdGenerator` to get predictable ids.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


class IdGenerator(Protocol):
    def new_id(self) -> str:
        """Return a new string identifier (UUID by default)."""


@dataclass(slots=True)
class UUID4IdGenerator:
    def new_id(self) -> str:
        return str(uuid4())


@dataclass(slots=True)
class SequentialIdGenerator:
    prefix: str = "id"
    counter: int = 0

    def new_id(self) -> str:
        self.counter += 1
        return f"{self.prefix}-{self.counter:08d}"
