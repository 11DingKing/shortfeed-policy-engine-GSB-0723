"""Historical replay service.

Given a finished (or in-flight) session, re-derive its decision trace by
replaying its recorded heartbeats against the *pinned* policy version. Because
both the policy document and the accounting rules are deterministic, the replay
reproduces the original settlement exactly — the tool used to audit disputes and
to prove that publishing new versions never rewrote history.
"""

from __future__ import annotations

from dataclasses import dataclass

from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.session import Session
from spe.domain.timeutil import local_day_key


@dataclass
class HeartbeatRecord:
    """A recorded heartbeat used as replay input."""

    occurred_at: object  # datetime; kept loose to avoid import churn
    seq: int
    watched_seconds_total: int


@dataclass
class ReplayStep:
    """One replayed heartbeat and the decision it produced."""

    seq: int
    credited_seconds: int
    local_day: str
    daily_usage_seconds: int
    total_watched_seconds: int
    allowed: bool
    reason: str
    trace: list[dict[str, str | None]]


def replay_session(
    session: Session,
    document: PolicyDocument,
    heartbeats: list[HeartbeatRecord],
    max_gap_seconds: int = 90,
) -> list[ReplayStep]:
    """Deterministically replay ``heartbeats`` for ``session`` against ``document``.

    The function mirrors :meth:`SessionService.heartbeat`'s accounting so a replay
    yields identical per-day settlement and decisions.
    """
    steps: list[ReplayStep] = []
    last_seq = 0
    marker = 0
    total = 0
    daily: dict[str, int] = {}

    for hb in sorted(heartbeats, key=lambda h: h.seq):
        if hb.seq <= last_seq:
            continue  # stale / duplicate / out-of-order
        raw_delta = hb.watched_seconds_total - marker
        delta = max(0, min(raw_delta, max_gap_seconds))
        local_day = local_day_key(hb.occurred_at, document.rules.timezone)  # type: ignore[arg-type]
        daily[local_day] = daily.get(local_day, 0) + delta
        total += delta
        marker = max(marker, hb.watched_seconds_total)
        last_seq = hb.seq

        ctx = EvalContext(
            now=hb.occurred_at,  # type: ignore[arg-type]
            user_id=session.user_id,
            user_age=130,
            daily_usage_seconds=daily[local_day],
            session_elapsed_seconds=total,
        )
        decision = evaluate(document, ctx)
        steps.append(
            ReplayStep(
                seq=hb.seq,
                credited_seconds=delta,
                local_day=local_day,
                daily_usage_seconds=daily[local_day],
                total_watched_seconds=total,
                allowed=decision.allowed,
                reason=decision.reason.value,
                trace=decision.trace.as_list(),
            )
        )
        if not decision.allowed:
            break  # session would have ended here

    return steps
