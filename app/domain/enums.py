from __future__ import annotations

from enum import Enum, StrEnum


class SessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    EXPIRED = "expired"


class SessionAction(StrEnum):
    START = "start"
    HEARTBEAT = "heartbeat"
    PAUSE = "pause"
    RESUME = "resume"
    END = "end"


class ReasonCode(StrEnum):
    OK = "ok"
    POLICY_NOT_FOUND = "policy_not_found"
    POLICY_VALIDATION_FAILED = "policy_validation_failed"
    AGE_GATE_BLOCKED = "age_gate_blocked"
    DAILY_LIMIT_EXCEEDED = "daily_limit_exceeded"
    SESSION_LIMIT_EXCEEDED = "session_limit_exceeded"
    BEDTIME_BAN_ACTIVE = "bedtime_ban_active"
    NOT_IN_TIME_WINDOW = "not_in_time_window"
    EXCEPTION_APPROVED = "exception_approved"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_ALREADY_ACTIVE = "session_already_active"
    SESSION_NOT_ACTIVE = "session_not_active"
    SESSION_ALREADY_ENDED = "session_already_ended"
    SESSION_ALREADY_PAUSED = "session_already_paused"
    SESSION_NOT_PAUSED = "session_not_paused"
    DUPLICATE_HEARTBEAT = "duplicate_heartbeat"
    HEARTBEAT_OUT_OF_ORDER = "heartbeat_out_of_order"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_POLICY_AST = "invalid_policy_ast"
    POLICY_VERSION_PINNED = "policy_version_pinned"
    CONCURRENT_SESSION_BLOCKED = "concurrent_session_blocked"
    CROSS_MIDNIGHT_RESET = "cross_midnight_reset"
    SESSION_EXPIRED = "session_expired"


REASON_CODE_MESSAGES: dict[ReasonCode, str] = {
    ReasonCode.OK: "Operation completed successfully.",
    ReasonCode.POLICY_NOT_FOUND: "The specified policy version does not exist for this tenant.",
    ReasonCode.POLICY_VALIDATION_FAILED: "The policy AST failed static validation checks.",
    ReasonCode.AGE_GATE_BLOCKED: "User does not meet the minimum age requirement.",
    ReasonCode.DAILY_LIMIT_EXCEEDED: "Daily usage limit has been reached for the user.",
    ReasonCode.SESSION_LIMIT_EXCEEDED: "Per-session duration limit has been reached.",
    ReasonCode.BEDTIME_BAN_ACTIVE: "Usage is blocked during the configured bedtime window.",
    ReasonCode.NOT_IN_TIME_WINDOW: "Current time is outside the allowed usage window.",
    ReasonCode.EXCEPTION_APPROVED: "User has an approved exception overriding the current rule.",
    ReasonCode.SESSION_NOT_FOUND: "The requested session does not exist.",
    ReasonCode.SESSION_ALREADY_ACTIVE: "An active session already exists for this user.",
    ReasonCode.SESSION_NOT_ACTIVE: "The session is not currently active.",
    ReasonCode.SESSION_ALREADY_ENDED: "The session has already been ended.",
    ReasonCode.SESSION_ALREADY_PAUSED: "The session is already paused.",
    ReasonCode.SESSION_NOT_PAUSED: "The session is not currently paused.",
    ReasonCode.DUPLICATE_HEARTBEAT: "A heartbeat with this sequence number was already processed.",
    ReasonCode.HEARTBEAT_OUT_OF_ORDER: "Heartbeat sequence number is older than last processed.",
    ReasonCode.IDEMPOTENCY_CONFLICT: "Idempotency key conflicts with a different prior request.",
    ReasonCode.TENANT_MISMATCH: "Resource belongs to a different tenant.",
    ReasonCode.INVALID_POLICY_AST: "The policy AST structure is syntactically invalid.",
    ReasonCode.POLICY_VERSION_PINNED: "Session is pinned to a specific policy version; new versions do not affect it.",
    ReasonCode.CONCURRENT_SESSION_BLOCKED: "Database constraint prevented creation of a concurrent active session.",
    ReasonCode.CROSS_MIDNIGHT_RESET: "Daily usage counters were reset upon crossing midnight in the user's timezone.",
    ReasonCode.SESSION_EXPIRED: "The session has expired due to inactivity or policy enforcement.",
}
