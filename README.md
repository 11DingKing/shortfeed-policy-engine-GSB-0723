# Shortfeed Policy Engine (`spe`)

A versioned, multi-tenant **short-video session policy engine**. It lets content
platforms express *who may watch, how much, and when* as a validatable JSON
policy AST, and enforces those rules across the full session lifecycle — start,
heartbeat, pause, resume, end and usage query — with strong consistency
guarantees.

## Guarantees

- **Version pinning** — a session records the policy version active at *start*
  and always settles against it. Publishing a new version never rewrites history.
- **Idempotent start** — repeating a start with the same idempotency key returns
  the original session.
- **Single active session** — enforced by a partial unique DB index, not just
  application code; concurrent starts collide at the database.
- **Ordered, idempotent heartbeats** — duplicate / out-of-order beats are ignored;
  only the new positive watch-time delta is credited (clamped per interval) and
  serialised via a `SELECT ... FOR UPDATE` row lock so concurrent beats can't
  double-count.
- **Budget-truncated crediting** — a heartbeat delta is truncated to what remains
  of *both* the session limit and the daily limit *before* it is written, so usage
  is never over-recorded and then rolled back. Hitting a hard limit ends the
  session with exactly the creditable amount booked.
- **Authoritative daily ledger** — daily usage lives in a per-`(tenant, user,
  local-day)` ledger, independent of any session. Ending a session and starting a
  new one on the same local day continues against the same quota — a user cannot
  reset their daily allowance by restarting.
- **Dynamic age** — sessions store a **birth date**; age is derived from the birth
  date and the current local date at every evaluation (start, heartbeat, replay).
  Nothing is hard-coded.
- **Precise cross-midnight split** — a heartbeat interval that straddles local
  midnight is divided to the second between the two local days.
- **Single-transaction consistency** — within one request the session mutation,
  ledger increment, heartbeat record, idempotency key and outbox event all commit
  in the *same* database transaction (transactional outbox).
- **Deterministic interpretation & replay** — evaluation is a pure function that
  emits a stable decision trace; a finished session can be replayed exactly.
- **Tenant isolation** — every query is scoped by `X-Tenant-ID`.
- **Process-restart safe** — all state lives in PostgreSQL; ids and time are
  injected, so restarts and tests are deterministic.

## Architecture (layered)

```
spe/
  api/           FastAPI transport: routes + wire schemas (no business logic)
  domain/        Pure business core: policy AST, validator, interpreter,
                 session aggregate, services, repository *ports* (Protocols)
  infra/db/      SQLAlchemy models, repositories (adapters), daily-usage ledger,
                 outbox, DDL
  container.py   Composition root — injects Clock + IdGenerator
  config.py      pydantic-settings configuration
alembic/         Versioned, reversible migrations
tests/           Unit + API + invariant + migration tests (SQLite + PostgreSQL)
```

Time (`Clock`) and identifiers (`IdGenerator`) are **injectable** — production
uses the system clock and UUIDs; tests inject a `FixedClock` and a sequential id
generator for full determinism.

## Requirements

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL, Alembic.
- A virtualenv is provided in `.venv`. Its console-script shebangs point at an
  old path, so invoke tools with `python -m` (e.g. `python -m alembic`), as shown
  below.

## Commands

### Setup

```bash
python -m venv .venv && source .venv/bin/activate   # if creating fresh
pip install -e ".[dev]"
```

### Run the API

```bash
# PostgreSQL in production:
export SPE_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/spe"
# (defaults to sqlite+aiosqlite:///./spe.db if unset)

python -m uvicorn spe.app:app --reload
# OpenAPI docs at http://localhost:8000/docs
```

### Migrations

```bash
export SPE_DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/spe"

python -m alembic upgrade head          # apply all migrations
python -m alembic current               # show current revision
python -m alembic downgrade -1          # roll back one migration
python -m alembic history               # list the migration chain
```

The migration chain is intentionally split so schema changes are **zero-downtime**
and every step is reversible:

- `0001_initial` — creates the base tables.
- `0002_active_session_guard` — adds the single-active-session partial unique
  index as a purely additive step (created `CONCURRENTLY` on PostgreSQL in real
  deployments).
- `0003_session_birth_date` — adds `sessions.birth_date` with a server default so
  existing rows backfill without a rewrite; old code keeps working.
- `0004_daily_usage_ledger` — creates the authoritative per-user
  `daily_usage_ledger`, folds existing per-session usage into it (`SUM` over each
  user's sessions per local day), then retires `session_daily_usage`. Its
  **downgrade is data-preserving**: the ledger totals are written back into a
  freshly recreated `session_daily_usage` (attributed to each user's most recent
  session), so `0004 → 0003 → 0004` neither drops nor double-counts consumed quota.

**Migration-history compatibility.** Published revision ids are immutable: once a
revision has been released it is never deleted, renamed, or rewritten, because a
database whose `alembic_version` already records it must always be able to locate
that revision and run `upgrade head`. In particular a database stamped at
`0004_daily_usage_ledger` upgrades to head as a no-op (regression-tested in
`test_third_round_db_can_upgrade_head_directly`).

The whole chain is reversible and data-preserving: `base → head → base → head`
with real data neither drops nor double-counts usage (covered by real-Postgres
round-trip tests).

### Tests & lint

```bash
python -m pytest -q          # full suite (unit + API + invariants + migrations)
python -m ruff check spe tests alembic
```

Fast unit/API tests run on SQLite. The invariants the spec requires to be proven
on the production engine — version pinning, tenant isolation, cross-day
settlement, process restart, concurrency, and the Alembic migrations — run
against **real PostgreSQL** (`tests/test_pg_invariants.py`, `tests/test_migrations.py`).
Point them at a database with `SPE_TEST_DATABASE_URL` (defaults to
`postgresql+asyncpg://postgres:postgres@localhost:55439/spe_test`); they skip
automatically if no database is reachable. Example with the bundled container:

```bash
export SPE_TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:55439/spe_test"
python -m pytest tests/test_pg_invariants.py tests/test_migrations.py -v
```

## Core endpoints

All endpoints require the `X-Tenant-ID` header.

| Method & path | Purpose |
|---|---|
| `POST /v1/policies/check` | Static-check a policy document without publishing. Returns issues. |
| `POST /v1/policies` | Validate and publish a new immutable policy version. |
| `POST /v1/policies/preview` | Dry-run the active (or a specific) policy against a hypothetical context (`user_id`, `birth_date`, optional usage/`at`/`version`); returns the decision + trace. |
| `POST /v1/sessions` | Start a session (pins the active version). Body: `user_id`, `birth_date`, optional `idempotency_key`. |
| `POST /v1/sessions/{id}/heartbeat` | Apply a heartbeat (`seq`, `watched_seconds_total`); credits the budget-bounded delta. Response `extra` includes `credited_seconds` and `per_day`. |
| `POST /v1/sessions/{id}/pause` | Pause an active session. |
| `POST /v1/sessions/{id}/resume` | Resume a paused session. |
| `POST /v1/sessions/{id}/end` | End a session (idempotent). |
| `GET  /v1/sessions/{id}/usage` | Query the session's total watch-time plus the authoritative daily total for the user's current local day (`extra.daily_today_seconds`). |
| `GET  /v1/sessions/{id}/replay` | Deterministically replay recorded heartbeats against the pinned policy (dynamic age, budget truncation, per-day split). |
| `GET  /v1/admin/health` | Liveness probe. |
| `POST /v1/admin/outbox/relay` | Publish pending outbox events (scheduler-triggered). |

### Response envelope

Session lifecycle actions return a uniform envelope:

```json
{
  "ok": true,
  "reason": "HEARTBEAT_APPLIED",
  "session": {
    "id": "…", "policy_version": 1, "status": "ACTIVE",
    "birth_date": "2006-07-24", "total_watched_seconds": 60
  },
  "trace": [],
  "extra": { "credited_seconds": 60, "per_day": { "2026-07-23": 30, "2026-07-24": 30 } }
}
```

`ok: false` with a 200 status means a *semantically valid* negative outcome
(e.g. a denial or an ignored stale heartbeat) — branch on `reason`. Genuine
not-found and conflict cases use HTTP `404` / `409` with `detail.reason`.

## Policy AST example

A policy is a JSON document validated both structurally (Pydantic) and
semantically (static checker). Example published to `POST /v1/policies`:

```json
{
  "document": {
    "name": "teens-weeknight",
    "rules": {
      "timezone": "America/New_York",
      "age_gate":      { "kind": "age_gate",      "min_age": 13 },
      "daily_limit":   { "kind": "daily_limit",   "max_seconds": 3600 },
      "session_limit": { "kind": "session_limit", "max_seconds": 1800 },
      "bedtime": {
        "kind": "bedtime",
        "windows": [ { "start": "22:00:00", "end": "06:00:00" } ]
      },
      "exceptions": [
        {
          "exception_id": "exc-001",
          "subject_user_id": "user-42",
          "waives": "bedtime",
          "approved_by": "guardian-7",
          "reason": "approved study-group livestream"
        }
      ]
    }
  }
}
```

Rules are evaluated in a **fixed, documented order**: `age_gate → bedtime →
daily_limit → session_limit`. The first denying rule short-circuits, unless an
approved exception for that user waives it (recorded in the trace as `waived`).
Bedtime windows may wrap past midnight (`start > end`), and all wall-clock rules
use the policy `timezone`. The `age_gate` compares against an age derived from the
session's `birth_date` and the current local date — so it is evaluated correctly
even for sessions that span a birthday.

Static checks (beyond schema validation) reject: unknown IANA timezones,
overlapping bedtime windows, a `session_limit` greater than the `daily_limit`
(unreachable), duplicate exception ids, and exceptions that waive a restriction
the policy does not define.

## Reason codes

Every decision and rejection carries a stable `ReasonCode` (string enum). Values
are a public contract and never change once released.

### Allow outcomes
| Code | Meaning |
|---|---|
| `ALLOWED` | Action permitted; no restriction applied. |
| `ALLOWED_BY_EXCEPTION` | An approved exception overrode a restriction that would otherwise deny. |

### Deny outcomes
| Code | Meaning |
|---|---|
| `DENIED_UNDER_MIN_AGE` | User's age is below the policy minimum. |
| `DENIED_DAILY_LIMIT_REACHED` | Per-day watch-time budget consumed. |
| `DENIED_SESSION_LIMIT_REACHED` | Session reached its maximum duration. |
| `DENIED_BEDTIME_CURFEW` | Local time falls inside a bedtime curfew window. |

### Lifecycle outcomes
| Code | Meaning |
|---|---|
| `SESSION_STARTED` | New session created and pinned to the active version. |
| `SESSION_STARTED_IDEMPOTENT` | Existing session returned for a repeated idempotency key. |
| `HEARTBEAT_APPLIED` | Heartbeat advanced usage accounting for an active session. |
| `HEARTBEAT_IGNORED_STALE` | Duplicate / out-of-order heartbeat ignored (no double counting). |
| `SESSION_PAUSED` | Session transitioned to paused. |
| `SESSION_RESUMED` | Session transitioned from paused back to active. |
| `SESSION_ENDED` | Session terminated and finalised; usage is now immutable. |
| `SESSION_ENDED_BY_LIMIT` | Session auto-ended because a hard limit was hit during a heartbeat. |

### Rejections (invalid requests / conflicts)
| Code | Meaning |
|---|---|
| `REJECTED_ACTIVE_SESSION_EXISTS` | Concurrent start lost the race; user already has an active session. |
| `REJECTED_SESSION_NOT_FOUND` | Session does not exist within the caller's tenant. |
| `REJECTED_SESSION_NOT_ACTIVE` | Action requires an active session but it is paused/ended. |
| `REJECTED_SESSION_NOT_PAUSED` | Resume requested on a session that is not paused. |
| `REJECTED_SESSION_ENDED` | Action not allowed because the session has already ended. |
| `REJECTED_POLICY_NOT_FOUND` | No published policy exists for the tenant (or requested version). |
| `REJECTED_POLICY_INVALID` | Submitted policy failed static validation. |
| `REJECTED_TENANT_MISMATCH` | Resource belongs to a different tenant than the caller. |
