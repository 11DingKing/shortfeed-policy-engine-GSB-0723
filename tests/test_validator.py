"""Tests for the static policy validator."""
from __future__ import annotations

from spe.domain.policy_ast import parse_policy
from spe.domain.policy_validator import validate_policy
from spe.domain.reason_codes import ReasonCode


def _minimal():
    return {
        "name": "min",
        "rules": [
            {"id": "a", "when": {"op": "true"}, "effect": {"kind": "allow"}},
        ],
    }


def test_valid_policy_has_no_issues(sample_policy):
    issues = validate_policy(parse_policy(sample_policy))
    assert issues == []


def test_unsat_age_contradiction_flagged():
    doc = {
        "name": "bad",
        "rules": [
            {
                "id": "r1",
                "when": {
                    "op": "and",
                    "children": [
                        {"op": "age_gte", "n": 18},
                        {"op": "age_lt", "n": 13},
                    ],
                },
                "effect": {"kind": "deny", "reason_code": "DENIED_AGE_BELOW_MINIMUM"},
            },
        ],
    }
    issues = validate_policy(parse_policy(doc))
    assert any(i.code is ReasonCode.POLICY_AST_UNREACHABLE_RULE for i in issues)


def test_shadowed_rule_flagged():
    doc = {
        "name": "shadow",
        "rules": [
            {"id": "r1", "when": {"op": "true"}, "effect": {"kind": "deny", "reason_code": "DENIED_NO_APPROVAL"}},
            {"id": "r2", "when": {"op": "age_lt", "n": 13}, "effect": {"kind": "allow"}},
        ],
    }
    issues = validate_policy(parse_policy(doc))
    assert any(i.rule_id == "r2" for i in issues)


def test_empty_bedtime_window_flagged():
    doc = {
        "name": "win",
        "rules": [
            {
                "id": "r1",
                "when": {"op": "local_time_between", "start": "22:00", "end": "22:00"},
                "effect": {"kind": "deny", "reason_code": "DENIED_BEDTIME_WINDOW"},
            },
        ],
    }
    issues = validate_policy(parse_policy(doc))
    assert any(i.code is ReasonCode.POLICY_AST_CONTRADICTION for i in issues)


def test_deny_no_approval_without_has_approval_flagged():
    doc = {
        "name": "m",
        "rules": [
            {
                "id": "r1",
                "when": {"op": "age_lt", "n": 18},
                "effect": {"kind": "deny", "reason_code": "DENIED_NO_APPROVAL"},
            },
        ],
    }
    issues = validate_policy(parse_policy(doc))
    assert any(i.code is ReasonCode.POLICY_AST_TYPE_ERROR for i in issues)
