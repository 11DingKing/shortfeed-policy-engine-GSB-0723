from __future__ import annotations

import uuid
from typing import Protocol


class IdGenerator(Protocol):
    def new_id(self) -> str: ...


class Uuid4Generator:
    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequentialGenerator:
    def __init__(self, prefix: str = "id") -> None:
        self._counter = 0
        self._prefix = prefix

    def new_id(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:06d}"
