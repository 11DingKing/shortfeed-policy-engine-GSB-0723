"""Static policy checker.

Performs three kinds of checks on a :class:`PolicyDocument`:

1. **Structural** — enforced by Pydantic (field types, unknown fields, unique
   rule ids, etc.).
2. **Satisfiability** — a rule whose predicate can never be true (for example
   ``age >= 18 AND age < 13`` or an empty bedtime window) is flagged as a
   contradiction.
3. **Reachability** — if an earlier rule already fires for every input on which
   a later rule would fire, the later rule is unreachable.

The satisfiability / reachability engine is a small *bounded model finder*: it
collects the constants that appear in the AST (age thresholds, timezones,
window boundaries, quota thresholds, approvals), picks representative boundary
values for each sort, and evaluates the boolean formula across the finite
cross-product.  For ASTs at human-authored scale this completes in milliseconds
yet catches the common authoring mistakes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import product
from zoneinfo import ZoneInfo

from .policy_ast import (
    AgeGte,
    AgeLt,
    AndNode,
    DailyUsedGte,
    EffectKind,
    EvaluationContext,
    Expr,
    FalseNode,
    HasApproval,
    LocalTimeBetween,
    NotNode,
    OrNode,
    PolicyDocument,
    SessionUsedGte,
    TimezoneIn,
    TrueNode,
)
from .policy_interpreter import _eval  # type: ignore[private-usage]
from .reason_codes import ReasonCode


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ReasonCode
    message: str
    rule_id: str | None = None


@dataclass(slots=True)
class _Constants:
    ages: set[int] = field(default_factory=set)
    zones: set[str] = field(default_factory=set)
    windows: list[tuple[int, int]] = field(default_factory=list)
    daily_seconds: set[int] = field(default_factory=set)
    session_seconds: set[int] = field(default_factory=set)
    approvals: set[str] = field(default_factory=set)


def _collect(node: Expr, c: _Constants) -> None:  # type: ignore[valid-type]
    if isinstance(node, (TrueNode, FalseNode)):
        return
    if isinstance(node, NotNode):
        _collect(node.child, c)
        return
    if isinstance(node, (AndNode, OrNode)):
        for ch in node.children:
            _collect(ch, c)
        return
    if isinstance(node, (AgeGte, AgeLt)):
        c.ages.add(node.n)
    elif isinstance(node, TimezoneIn):
        c.zones.update(node.zones)
    elif isinstance(node, LocalTimeBetween):
        sh, sm = (int(x) for x in node.start.split(":"))
        eh, em = (int(x) for x in node.end.split(":"))
        c.windows.append((sh * 60 + sm, eh * 60 + em))
    elif isinstance(node, DailyUsedGte):
        c.daily_seconds.add(node.seconds)
    elif isinstance(node, SessionUsedGte):
        c.session_seconds.add(node.seconds)
    elif isinstance(node, HasApproval):
        c.approvals.add(node.approval)


def _rep_ages(thresholds: set[int]) -> list[int]:
    vals: set[int] = {0, 150}
    for n in thresholds:
        for cand in (n - 1, n, n + 1):
            if 0 <= cand <= 150:
                vals.add(cand)
    return sorted(vals)


def _rep_minutes(windows: list[tuple[int, int]]) -> list[int]:
    vals: set[int] = {12 * 60}  # noon as neutral
    for s, e in windows:
        for cand in (s - 1, s, (s + 1) % (24 * 60), e - 1, e, (e + 1) % (24 * 60)):
            if 0 <= cand < 24 * 60:
                vals.add(cand)
    return sorted(v for v in vals if 0 <= v < 24 * 60)


def _rep_seconds(thresholds: set[int]) -> list[int]:
    vals: set[int] = {0}
    for n in thresholds:
        for cand in (max(0, n - 1), n, n + 1):
            vals.add(cand)
    return sorted(vals)


def _rep_zones(zones: set[str]) -> list[str]:
    reps = list(zones)
    reps.append("Etc/UTC")  # a zone that is not in any mentioned set
    # dedup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for z in reps:
        if z not in seen:
            seen.add(z)
            out.append(z)
    return out


def _utc_for_local_minute(local_minute: int, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    local = datetime(2026, 1, 15, local_minute // 60 % 24, local_minute % 60, 0, tzinfo=tz)
    return local.astimezone(UTC)


def _satisfiable(predicate: Expr, c: _Constants, *, extra_negatives: list[Expr] | None = None) -> bool:  # type: ignore[valid-type]
    """Return True iff there exists a context where ``predicate`` is true.

    If ``extra_negatives`` is supplied, the check is ``predicate AND NOT n1 AND
    NOT n2 ...`` which is used for reachability: rule i is reachable only if
    there is some context where its predicate is true and no earlier rule j's
    predicate is true.
    """
    ages = _rep_ages(c.ages)
    minutes = _rep_minutes(c.windows)
    zones = _rep_zones(c.zones)
    daily = _rep_seconds(c.daily_seconds)
    session = _rep_seconds(c.session_seconds)
    approval_opts: list[set[str]] = [set()]
    for a in c.approvals:
        new_opts = []
        for existing in approval_opts:
            new_opts.append(existing)
            new_opts.append(existing | {a})
        approval_opts = new_opts

    # Cap the enumeration to keep pathological policies responsive.
    cap = 8000
    counted = 0
    for age, minute, tz, ds, ss, approvals in product(
        ages, minutes, zones, daily, session, approval_opts
    ):
        counted += 1
        if counted > cap:
            # Bail out and assume satisfiable (sound-incomplete: don't false-positive).
            return True
        now_utc = _utc_for_local_minute(minute, tz)
        ctx = EvaluationContext(
            user_age=age,
            user_timezone=tz,
            now_utc=now_utc,
            daily_used_seconds=ds,
            session_used_seconds=ss,
            approvals=set(approvals),
        )
        trace: list = []
        if not _eval(predicate, ctx, trace, None):
            continue
        ok = True
        for neg in extra_negatives or []:
            trace2: list = []
            if _eval(neg, ctx, trace2, None):
                ok = False
                break
        if ok:
            return True
    return False


def validate_policy(doc: PolicyDocument) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    # --- per-rule checks ---------------------------------------------------
    for rule in doc.rules:
        c = _Constants()
        _collect(rule.when, c)

        # Empty bedtime window → predicate always false → rule dead.
        if isinstance(rule.when, LocalTimeBetween) and rule.when.start == rule.when.end:
            issues.append(
                ValidationIssue(
                    ReasonCode.POLICY_AST_CONTRADICTION,
                    f"rule {rule.id!r}: bedtime window {rule.when.start}-{rule.when.end} is empty",
                    rule.id,
                )
            )

        # A Deny rule whose effect is tied to approval should actually reference
        # the approval in its predicate, otherwise it's a misconfiguration.
        if (
            rule.effect.kind is EffectKind.DENY
            and rule.effect.reason_code is ReasonCode.DENIED_NO_APPROVAL
            and not _mentions(rule.when, HasApproval)
        ):
            issues.append(
                ValidationIssue(
                    ReasonCode.POLICY_AST_TYPE_ERROR,
                    f"rule {rule.id!r}: deny reason NO_APPROVAL but predicate never checks HasApproval",
                    rule.id,
                )
            )

        # Is the rule satisfiable at all?
        if not _satisfiable(rule.when, c):
            issues.append(
                ValidationIssue(
                    ReasonCode.POLICY_AST_UNREACHABLE_RULE,
                    f"rule {rule.id!r}: predicate is unsatisfiable (dead rule)",
                    rule.id,
                )
            )

    # --- reachability across rules ----------------------------------------
    # Collect every earlier predicate to test reachability of rule i.
    collected_constants = _Constants()
    for rule in doc.rules:
        _collect(rule.when, collected_constants)

    earlier: list[Expr] = []  # type: ignore[valid-type]
    for rule in doc.rules:
        if earlier and not _satisfiable(
            rule.when, collected_constants, extra_negatives=list(earlier)
        ):
            issues.append(
                ValidationIssue(
                    ReasonCode.POLICY_AST_UNREACHABLE_RULE,
                    f"rule {rule.id!r} is shadowed by earlier rule(s) and can never fire",
                    rule.id,
                )
            )
        earlier.append(rule.when)

    return issues


def _mentions(node: Expr, kind: type) -> bool:  # type: ignore[valid-type]
    if isinstance(node, kind):
        return True
    if isinstance(node, NotNode):
        return _mentions(node.child, kind)
    if isinstance(node, (AndNode, OrNode)):
        return any(_mentions(ch, kind) for ch in node.children)
    return False


def preview(doc: PolicyDocument, ctx: EvaluationContext):
    """Return both the static issues and a concrete evaluation for preview."""
    from .policy_interpreter import evaluate

    issues = validate_policy(doc)
    result = evaluate(doc, ctx)
    return issues, result
