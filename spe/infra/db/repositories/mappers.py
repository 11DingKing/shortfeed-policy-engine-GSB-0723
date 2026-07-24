"""Mapping helpers between ORM rows and domain objects."""

from __future__ import annotations

from spe.domain.policy_ast import PolicyDocument
from spe.domain.session import DailyUsage, Session, SessionStatus
from spe.infra.db.models import DailyUsageModel, PolicyModel, SessionModel


class PolicyRecordAdapter:
    """Wraps a :class:`PolicyModel` to satisfy the domain ``PolicyRecord`` port."""

    def __init__(self, model: PolicyModel) -> None:
        self._model = model
        self._document = PolicyDocument.model_validate(model.document)

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def tenant_id(self) -> str:
        return self._model.tenant_id

    @property
    def version(self) -> int:
        return self._model.version

    @property
    def document(self) -> PolicyDocument:
        return self._document


def session_to_domain(model: SessionModel) -> Session:
    session = Session(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        policy_id=model.policy_id,
        policy_version=model.policy_version,
        status=SessionStatus(model.status),
        started_at=model.started_at,
        updated_at=model.updated_at,
        ended_at=model.ended_at,
        last_seq=model.last_seq,
        watched_seconds_marker=model.watched_seconds_marker,
        total_watched_seconds=model.total_watched_seconds,
    )
    for bucket in model.daily_usage:
        session.daily[bucket.local_day] = DailyUsage(
            local_day=bucket.local_day, seconds=bucket.seconds
        )
    return session


def apply_session_to_model(session: Session, model: SessionModel) -> None:
    model.status = session.status.value
    model.updated_at = session.updated_at
    model.ended_at = session.ended_at
    model.last_seq = session.last_seq
    model.watched_seconds_marker = session.watched_seconds_marker
    model.total_watched_seconds = session.total_watched_seconds

    existing = {b.local_day: b for b in model.daily_usage}
    for day, usage in session.daily.items():
        row = existing.get(day)
        if row is None:
            model.daily_usage.append(
                DailyUsageModel(
                    session_id=session.id,
                    tenant_id=session.tenant_id,
                    local_day=day,
                    seconds=usage.seconds,
                )
            )
        else:
            row.seconds = usage.seconds
