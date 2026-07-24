"""Reason codes produced by the policy engine.

Every outcome of the policy interpreter, session service, and API layer is
expressed as a member of :class:`ReasonCode` so that clients and tests can
branch on stable string values rather than human-readable prose.
"""
from __future__ import annotations

from enum import Enum


class ReasonCode(str, Enum):
    # Generic allow / neutral outcomes
    OK = "OK"
    ALLOWED = "ALLOWED"
    VERSION_PINNED = "VERSION_PINNED"

    # Policy lifecycle
    POLICY_CREATED = "POLICY_CREATED"
    POLICY_PUBLISHED = "POLICY_PUBLISHED"
    POLICY_PREVIEW_OK = "POLICY_PREVIEW_OK"
    POLICY_VALID = "POLICY_VALID"

    # Session lifecycle
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_PAUSED = "SESSION_PAUSED"
    SESSION_RESUMED = "SESSION_RESUMED"
    SESSION_ENDED = "SESSION_ENDED"
    HEARTBEAT_ACCEPTED = "HEARTBEAT_ACCEPTED"
    USAGE_QUERIED = "USAGE_QUERIED"

    # Denials enforced by policy
    DENIED_AGE_BELOW_MINIMUM = "DENIED_AGE_BELOW_MINIMUM"
    DENIED_AGE_ABOVE_MAXIMUM = "DENIED_AGE_ABOVE_MAXIMUM"
    DENIED_TIMEZONE_FORBIDDEN = "DENIED_TIMEZONE_FORBIDDEN"
    DENIED_BEDTIME_WINDOW = "DENIED_BEDTIME_WINDOW"
    DENIED_NO_APPROVAL = "DENIED_NO_APPROVAL"

    # Quota limits
    LIMITED_DAILY_QUOTA_REACHED = "LIMITED_DAILY_QUOTA_REACHED"
    LIMITED_SESSION_QUOTA_REACHED = "LIMITED_SESSION_QUOTA_REACHED"

    # Idempotency / ordering
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
    HEARTBEAT_OUT_OF_ORDER = "HEARTBEAT_OUT_OF_ORDER"
    HEARTBEAT_DUPLICATE = "HEARTBEAT_DUPLICATE"
    HEARTBEAT_STALE = "HEARTBEAT_STALE"

    # State machine violations
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_ALREADY_ENDED = "SESSION_ALREADY_ENDED"
    SESSION_ALREADY_ACTIVE = "SESSION_ALREADY_ACTIVE"
    SESSION_NOT_ACTIVE = "SESSION_NOT_ACTIVE"
    SESSION_NOT_PAUSED = "SESSION_NOT_PAUSED"
    HEARTBEAT_PROHIBITED_WHEN_PAUSED = "HEARTBEAT_PROHIBITED_WHEN_PAUSED"
    CONCURRENT_SESSION_BLOCKED = "CONCURRENT_SESSION_BLOCKED"

    # Tenant isolation
    TENANT_ISOLATION_VIOLATION = "TENANT_ISOLATION_VIOLATION"

    # Errors
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_VERSION_NOT_FOUND = "POLICY_VERSION_NOT_FOUND"
    POLICY_INVALID = "POLICY_INVALID"
    POLICY_AST_TYPE_ERROR = "POLICY_AST_TYPE_ERROR"
    POLICY_AST_UNREACHABLE_RULE = "POLICY_AST_UNREACHABLE_RULE"
    POLICY_AST_CONTRADICTION = "POLICY_AST_CONTRADICTION"
    INVALID_TRANSITION = "INVALID_TRANSITION"

    # Time / cross-midnight
    CROSS_MIDNIGHT_DAILY_RESET = "CROSS_MIDNIGHT_DAILY_RESET"

    # Replay
    REPLAY_COMPLETED = "REPLAY_COMPLETED"


# Human-readable descriptions for documentation and API responses.
REASON_CODE_DESCRIPTIONS: dict[ReasonCode, str] = {
    ReasonCode.OK: "Operation completed successfully.",
    ReasonCode.ALLOWED: "Policy explicitly allows the action.",
    ReasonCode.VERSION_PINNED: "Session is pinned to the policy version that was active when it started; a newly published version does not affect it.",
    ReasonCode.POLICY_CREATED: "A new (draft) policy was created.",
    ReasonCode.POLICY_PUBLISHED: "A new immutable policy version was published; existing sessions keep their previously pinned version.",
    ReasonCode.POLICY_PREVIEW_OK: "The supplied AST was statically checked and previewed against a sample context without errors.",
    ReasonCode.POLICY_VALID: "The AST passed all static checks (types, contradictions, reachability).",
    ReasonCode.SESSION_STARTED: "A new session was created and pinned to the currently published policy version.",
    ReasonCode.SESSION_PAUSED: "An active session was paused; time accumulation stops until resume.",
    ReasonCode.SESSION_RESUMED: "A paused session was resumed; time accumulation restarts.",
    ReasonCode.SESSION_ENDED: "The session was ended. Ending is idempotent.",
    ReasonCode.HEARTBEAT_ACCEPTED: "The heartbeat was accepted and usage counters advanced.",
    ReasonCode.USAGE_QUERIED: "Usage query returned current counters.",
    ReasonCode.DENIED_AGE_BELOW_MINIMUM: "User age is below the minimum age required by a DENY rule.",
    ReasonCode.DENIED_AGE_ABOVE_MAXIMUM: "User age is above the maximum age permitted by a DENY rule.",
    ReasonCode.DENIED_TIMEZONE_FORBIDDEN: "User timezone is in the forbidden set.",
    ReasonCode.DENIED_BEDTIME_WINDOW: "Current local time falls inside a configured no-scroll bedtime window.",
    ReasonCode.DENIED_NO_APPROVAL: "Action requires an exception approval that is not present or not active.",
    ReasonCode.LIMITED_DAILY_QUOTA_REACHED: "Daily usage (in the user's local day) has reached the configured ceiling.",
    ReasonCode.LIMITED_SESSION_QUOTA_REACHED: "Per-session usage has reached the configured ceiling.",
    ReasonCode.IDEMPOTENT_REPLAY: "A previous request with the same idempotency key was replayed from durable storage.",
    ReasonCode.HEARTBEAT_OUT_OF_ORDER: "Heartbeat sequence number is earlier than the last processed one; it was ignored.",
    ReasonCode.HEARTBEAT_DUPLICATE: "Heartbeat with this sequence number has already been processed; it was ignored.",
    ReasonCode.HEARTBEAT_STALE: "Heartbeat timestamp is too far in the past to be applied.",
    ReasonCode.SESSION_NOT_FOUND: "No session exists with the supplied id for this tenant.",
    ReasonCode.SESSION_ALREADY_ENDED: "Session has already ended; the operation is a no-op.",
    ReasonCode.SESSION_ALREADY_ACTIVE: "Session is already active; a second start is rejected.",
    ReasonCode.SESSION_NOT_ACTIVE: "Operation requires an ACTIVE session but it is in another state.",
    ReasonCode.SESSION_NOT_PAUSED: "Operation requires a PAUSED session but it is in another state.",
    ReasonCode.HEARTBEAT_PROHIBITED_WHEN_PAUSED: "Heartbeats are not accepted while a session is paused.",
    ReasonCode.CONCURRENT_SESSION_BLOCKED: "A unique database constraint blocked creating a second active/paused session for the same user.",
    ReasonCode.TENANT_ISOLATION_VIOLATION: "Attempted to access a resource belonging to another tenant.",
    ReasonCode.POLICY_NOT_FOUND: "No policy exists with the supplied id.",
    ReasonCode.POLICY_VERSION_NOT_FOUND: "The requested policy version does not exist.",
    ReasonCode.POLICY_INVALID: "The AST failed static validation.",
    ReasonCode.POLICY_AST_TYPE_ERROR: "An AST node has the wrong argument type.",
    ReasonCode.POLICY_AST_UNREACHABLE_RULE: "A rule is shadowed by an earlier rule and can never fire.",
    ReasonCode.POLICY_AST_CONTRADICTION: "The policy contains rules that contradict each other (e.g. min_age > max_age).",
    ReasonCode.INVALID_TRANSITION: "Requested state transition is not allowed by the session state machine.",
    ReasonCode.CROSS_MIDNIGHT_DAILY_RESET: "A heartbeat crossed the user's local midnight; the daily bucket was rolled over.",
    ReasonCode.REPLAY_COMPLETED: "Historical replay of a session completed and produced a deterministic trace.",
}
