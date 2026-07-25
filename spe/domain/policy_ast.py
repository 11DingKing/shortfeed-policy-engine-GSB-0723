"""Policy AST.

A policy is a small, declarative, JSON-serialisable abstract syntax tree. It is
authored by tenant admins, validated statically before it can be published, and
interpreted deterministically at session time.

The AST intentionally models *rules as data* rather than code so that:

* every field can be validated up-front (see :mod:`spe.domain.policy_validator`);
* a published version can be stored verbatim and replayed byte-for-byte later;
* the interpreter can produce a stable, explainable decision trace.

Expressible concepts:

* **age gate** — a minimum age required to watch at all;
* **timezone** — the IANA zone used to localise all wall-clock rules;
* **daily limit** — total watch-time budget that resets at local midnight;
* **session limit** — maximum duration of a single session;
* **bedtime curfew** — one or more local time windows where watching is blocked;
* **exceptions** — approved overrides that relax specific restrictions.
"""

from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# --- Reusable constrained types --------------------------------------------

Minutes = Annotated[int, Field(ge=0, le=24 * 60)]
"""A duration expressed in whole minutes within a single day."""

Seconds = Annotated[int, Field(ge=0, le=24 * 60 * 60)]
"""A duration expressed in whole seconds within a single day."""


class RestrictionKind(StrEnum):
    """The categories of restriction an exception may waive."""

    MIN_AGE = "min_age"
    DAILY_LIMIT = "daily_limit"
    SESSION_LIMIT = "session_limit"
    BEDTIME = "bedtime"


# --- Leaf rule nodes --------------------------------------------------------


class AgeGate(BaseModel):
    """Minimum age required before any watching is permitted."""

    model_config = {"extra": "forbid"}

    kind: Literal["age_gate"] = "age_gate"
    min_age: int = Field(ge=0, le=130)


class DailyLimit(BaseModel):
    """Total watch-time budget per local calendar day."""

    model_config = {"extra": "forbid"}

    kind: Literal["daily_limit"] = "daily_limit"
    max_seconds: Seconds


class SessionLimit(BaseModel):
    """Maximum duration of a single session."""

    model_config = {"extra": "forbid"}

    kind: Literal["session_limit"] = "session_limit"
    max_seconds: Seconds


class BedtimeWindow(BaseModel):
    """A single local-time curfew interval.

    ``start`` and ``end`` are local wall-clock times. When ``start`` > ``end``
    the window wraps past midnight (e.g. 22:00 -> 06:00).
    """

    model_config = {"extra": "forbid"}

    start: time
    end: time

    @model_validator(mode="after")
    def _reject_empty(self) -> BedtimeWindow:
        if self.start == self.end:
            raise ValueError("bedtime window start and end must differ")
        return self

    def contains(self, local: time) -> bool:
        """Return whether a local time falls inside this (possibly wrapping) window."""
        if self.start <= self.end:
            return self.start <= local < self.end
        # Wrapping window: inside if after start OR before end.
        return local >= self.start or local < self.end


class BedtimeCurfew(BaseModel):
    """One or more bedtime windows during which watching is blocked."""

    model_config = {"extra": "forbid"}

    kind: Literal["bedtime"] = "bedtime"
    windows: list[BedtimeWindow] = Field(min_length=1)


# --- Exceptions -------------------------------------------------------------


class ApprovedException(BaseModel):
    """An approved override that waives a specific restriction for a user.

    Exceptions carry provenance (who approved, when) so the audit trail and the
    replay are complete. An exception only relaxes the restriction named by
    ``waives``; it never grants more than the underlying policy would.
    """

    model_config = {"extra": "forbid"}

    exception_id: str
    subject_user_id: str
    waives: RestrictionKind
    approved_by: str
    reason: str = ""


# --- Root document ----------------------------------------------------------


class PolicyRules(BaseModel):
    """The rule set of a policy version."""

    model_config = {"extra": "forbid"}

    timezone: str = "UTC"
    age_gate: AgeGate | None = None
    daily_limit: DailyLimit | None = None
    session_limit: SessionLimit | None = None
    bedtime: BedtimeCurfew | None = None
    exceptions: list[ApprovedException] = Field(default_factory=list)


class PolicyDocument(BaseModel):
    """A complete, publishable policy document (the versioned AST root).

    A published version freezes this document; sessions pin the ``version`` they
    started with so later publishes never alter historical settlement.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    rules: PolicyRules
