"""Deterministic policy interpreter.

Evaluates a :class:`PolicyDocument` against an :class:`EvaluationContext` and
produces an :class:`EvaluationResult`.  Rules are evaluated in order; the first
rule whose predicate matches wins.  If no rule matches the default decision is
``ALLOW``.

Every leaf / boolean node that is visited appends a :class:`TraceEntry` so that
the same inputs always byte-for-byte reproduce the same explanation.  This is
what powers both the live ``/preview`` endpoint and the historical ``/replay``
endpoint: given the stored document + stored context we simply re-run
:func:`evaluate`.
"""
from __future__ import annotations

from .policy_ast import (
    AgeGte,
    AgeLt,
    AndNode,
    DailyUsedGte,
    EffectKind,
    EvaluationContext,
    EvaluationResult,
    Expr,
    FalseNode,
    HasApproval,
    LimitDailyEffect,
    LimitSessionEffect,
    LocalTimeBetween,
    NodeType,
    NotNode,
    OrNode,
    PolicyDocument,
    Rule,
    SessionUsedGte,
    TimezoneIn,
    TraceEntry,
    TrueNode,
)
from .reason_codes import ReasonCode


# The module is imported heavily; alias the context under a shorter name for the
# evaluator without shadowing the public name.
def _eval(node: Expr, ctx: EvaluationContext, trace: list[TraceEntry], rule_id: str | None) -> bool:  # type: ignore[valid-type]
    if isinstance(node, TrueNode):
        v = True
    elif isinstance(node, FalseNode):
        v = False
    elif isinstance(node, NotNode):
        v = not _eval(node.child, ctx, trace, rule_id)
    elif isinstance(node, AndNode):
        v = True
        for c in node.children:
            if not _eval(c, ctx, trace, rule_id):
                v = False
                # We still evaluate the remaining children so that the trace is
                # *deterministic and total* (independent of short-circuit).
                # This means trace length is stable for a given tree shape.
        return _record(trace, rule_id, NodeType.AND, v, f"children={len(node.children)}")
    elif isinstance(node, OrNode):
        v = False
        for c in node.children:
            if _eval(c, ctx, trace, rule_id):
                v = True
        return _record(trace, rule_id, NodeType.OR, v, f"children={len(node.children)}")
    elif isinstance(node, AgeGte):
        v = ctx.user_age >= node.n
    elif isinstance(node, AgeLt):
        v = ctx.user_age < node.n
    elif isinstance(node, TimezoneIn):
        v = ctx.user_timezone in node.zones
    elif isinstance(node, LocalTimeBetween):
        v = _local_time_in_window(ctx, node.start, node.end)
    elif isinstance(node, DailyUsedGte):
        v = ctx.daily_used_seconds >= node.seconds
    elif isinstance(node, SessionUsedGte):
        v = ctx.session_used_seconds >= node.seconds
    elif isinstance(node, HasApproval):
        v = node.approval in ctx.approvals
    else:  # pragma: no cover - discriminated union is exhaustive
        raise TypeError(f"unexpected node: {node!r}")
    return _record(trace, rule_id, node.op, v, _leaf_detail(node, ctx))  # type: ignore[attr-defined]


def _record(trace: list[TraceEntry], rule_id: str | None, op: NodeType, value: bool, detail: str) -> bool:
    trace.append(TraceEntry(rule_id=rule_id, node=op, result=value, detail=detail))
    return value


def _leaf_detail(node: Expr, ctx: EvaluationContext) -> str:  # type: ignore[valid-type]
    if isinstance(node, AgeGte):
        return f"age={ctx.user_age} >= {node.n}"
    if isinstance(node, AgeLt):
        return f"age={ctx.user_age} < {node.n}"
    if isinstance(node, TimezoneIn):
        return f"tz={ctx.user_timezone!r} in {node.zones}"
    if isinstance(node, LocalTimeBetween):
        local = ctx.local_now().strftime("%H:%M")
        return f"local={local} window={node.start}-{node.end}"
    if isinstance(node, DailyUsedGte):
        return f"daily_used={ctx.daily_used_seconds} >= {node.seconds}"
    if isinstance(node, SessionUsedGte):
        return f"session_used={ctx.session_used_seconds} >= {node.seconds}"
    if isinstance(node, HasApproval):
        return f"approval={node.approval!r} present={node.approval in ctx.approvals}"
    return ""


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _local_time_in_window(ctx: EvaluationContext, start: str, end: str) -> bool:
    now_local = ctx.local_now()
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    cur_min = now_local.hour * 60 + now_local.minute
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= cur_min < end_min
    # wrap around midnight
    return cur_min >= start_min or cur_min < end_min


def evaluate(doc: PolicyDocument, ctx: EvaluationContext) -> EvaluationResult:
    trace: list[TraceEntry] = []
    for rule in doc.rules:
        if _eval(rule.when, ctx, trace, rule.id):
            return _fire(rule, trace)
    trace.append(TraceEntry(rule_id=None, node=NodeType.TRUE, result=True, detail="default allow"))
    return EvaluationResult(
        allowed=True,
        rule_id=None,
        reason=ReasonCode.ALLOWED.value,
        effect=EffectKind.ALLOW,
        limit_seconds=None,
        trace=trace,
    )


def _fire(rule: Rule, trace: list[TraceEntry]) -> EvaluationResult:
    eff = rule.effect
    if eff.kind is EffectKind.ALLOW:
        return EvaluationResult(
            allowed=True,
            rule_id=rule.id,
            reason=ReasonCode.ALLOWED.value,
            effect=EffectKind.ALLOW,
            trace=trace,
        )
    if eff.kind is EffectKind.DENY:
        return EvaluationResult(
            allowed=False,
            rule_id=rule.id,
            reason=eff.reason_code.value,
            effect=EffectKind.DENY,
            trace=trace,
        )
    if eff.kind is EffectKind.LIMIT_DAILY:
        assert isinstance(eff, LimitDailyEffect)
        return EvaluationResult(
            allowed=False,
            rule_id=rule.id,
            reason=ReasonCode.LIMITED_DAILY_QUOTA_REACHED.value,
            effect=EffectKind.LIMIT_DAILY,
            limit_seconds=eff.seconds,
            trace=trace,
        )
    if eff.kind is EffectKind.LIMIT_SESSION:
        assert isinstance(eff, LimitSessionEffect)
        return EvaluationResult(
            allowed=False,
            rule_id=rule.id,
            reason=ReasonCode.LIMITED_SESSION_QUOTA_REACHED.value,
            effect=EffectKind.LIMIT_SESSION,
            limit_seconds=eff.seconds,
            trace=trace,
        )
    raise TypeError(f"unknown effect kind: {eff.kind!r}")  # pragma: no cover
