# shortfeed-policy-engine

多租户短视频会话策略引擎。把年龄、时区、每日/单次限额、睡前禁刷、例外审批表达为**可静态校验的 JSON 策略 AST**，并提供策略的版本化发布、规则预览，以及会话的开始、心跳、暂停、恢复、结束与用量查询。每个会话在启动时固定使用当时发布的策略版本（policy version pinning），发布新版本绝不影响历史结算。

* 语言 / 框架：Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL / SQLite
* 分层：`api`（HTTP） / `domain`（纯业务） / `infra.db`（仓储、事务、Outbox）
* 注入点：`Clock`、`IdGenerator` 均为 Protocol，可注入 FakeClock / SequentialIdGenerator 做确定性测试与历史重放
* 一致性：
  * 幂等键（idempotency key）持久化，重复请求直接回放
  * 数据库部分唯一索引禁止同一用户同时存在两个 ACTIVE/PAUSED 会话
  * 心跳用客户端单调序号去重 / 拒绝乱序；暂停态拒绝心跳
  * 所有表都带 `tenant_id`，租户隔离在仓储查询与应用层双重保证
  * 心跳跨越用户本地零点时按本地日期拆分计入每日用量桶
  * 状态、心跳、Outbox 事件、幂等记录在一个事务里提交，进程重启后状态一致
* 可观测性：每次策略评估返回**确定性解释轨迹**（TraceEntry 列表），可用于审计与回放

---

## 目录结构

```
spe/
  api/               # FastAPI 路由、依赖、请求/响应模型
    routes/          # policies / sessions / admin(health, reason-codes, replay)
    schemas/         # Pydantic v2 HTTP schemas
  domain/            # 纯业务逻辑（无 IO）
    policy_ast.py        # JSON AST 模型（判别联合）
    policy_interpreter.py# 确定性解释器 + trace
    policy_validator.py  # 静态检查（类型、矛盾、不可达规则）
    session.py           # 会话聚合根 + 状态机
    services/            # PolicyService / SessionService / ReplayService
    reason_codes.py      # 全量原因码枚举
    clock.py / ids.py    # 可注入的时钟与 ID 生成器
    timeutil.py          # 时区与跨零点拆分
    events.py            # 领域事件
    repositories.py      # 仓储协议
  infra/db/          # SQLAlchemy 2 ORM、仓储实现、UoW、Outbox
    models.py
    repositories/
    uow.py
    outbox.py
    session.py
  container.py       # 依赖注入装配
  app.py             # FastAPI 工厂
alembic/             # 数据库迁移（expand/contract）
tests/               # pytest + pytest-asyncio
```

---

## 快速开始

### 1. 安装

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

默认使用本地 SQLite（`sqlite+aiosqlite:///./spe.db`）。生产使用 PostgreSQL 时设置环境变量：

```bash
export SPE_DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/spe"
```

### 2. 启动 API 服务

```bash
# 方式一：直接运行（应用启动时自动 create_all 建表，适合本地开发）
python -m spe
#   或
uvicorn spe.app:app --host 0.0.0.0 --port 8000

# 方式二：用 Alembic 迁移到最新版本后启动（推荐生产）
alembic upgrade head
uvicorn spe.app:app --host 0.0.0.0 --port 8000
```

打开 OpenAPI 文档：`http://localhost:8000/docs`。

所有业务接口都需要请求头 `X-Tenant-Id: <tenant>`，该字段只从 Header 读取，不会从 JSON Body 接收，以防跨租户伪造。

### 3. 运行测试

```bash
pytest                # 全部 43 个用例
pytest -q             # 简洁输出
pytest tests/test_session_service.py -q   # 单个文件
```

### 4. 代码检查

```bash
ruff check spe tests
```

### 5. 数据库迁移命令

```bash
# 升级到最新版
alembic upgrade head

# 升级到指定版本
alembic upgrade 0002_expand_policies_md

# 回滚一个版本
alembic downgrade -1

# 回滚到基线（清空）
alembic downgrade base

# 查看当前版本
alembic current

# 查看历史
alembic history

# 自动生成新迁移（生产可在此基础上 review）
alembic revision --autogenerate -m "add column x"
```

迁移采用 **expand / contract（零停机）** 模式，见 [alembic/versions/0002_expand_policies_md.py](alembic/versions/0002_expand_policies_md.py) 与 [0003_contract_policies_md.py](alembic/versions/0003_contract_policies_md.py)：先加列（expand，旧代码继续可用），再收紧约束（contract，等所有节点部署完成）。对应测试见 [test_migrations.py](tests/test_migrations.py)。

### 6. 使用 Docker（可选）

```bash
# 启动 Postgres
docker run -d --name spe-pg -e POSTGRES_USER=spe -e POSTGRES_PASSWORD=spe -e POSTGRES_DB=spe -p 5432:5432 postgres:16
export SPE_DATABASE_URL="postgresql+asyncpg://spe:spe@localhost:5432/spe"
alembic upgrade head
uvicorn spe.app:app --host 0.0.0.0 --port 8000
```

---

## 核心 HTTP 端点

所有路径都有 `/api/v1` 前缀。响应统一为：

```json
{ "ok": true, "reason": "REASON_CODE", "message": "human readable", "data": { ... } }
```

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/policies` | 创建策略并发布首个版本（带 idempotency_key） |
| POST | `/policies/{policy_id}/versions` | 发布新版本（不可变，历史会话继续用旧版本） |
| GET  | `/policies/{policy_id}/current` | 获取当前发布版本的 AST |
| POST | `/policies/validate` | 仅做静态校验，不落库 |
| POST | `/policies/preview` | 给定 context 计算策略结果与解释轨迹，不落库 |
| POST | `/sessions/start` | 开始会话，固定当前策略版本 |
| POST | `/sessions/{id}/heartbeat` | 心跳（body: `{sequence, idempotency_key?}`） |
| POST | `/sessions/{id}/pause`    | 暂停 |
| POST | `/sessions/{id}/resume`   | 恢复 |
| POST | `/sessions/{id}/end`      | 结束（幂等） |
| GET  | `/usage?user_id=&user_timezone=` | 查询今日/当前会话用量 |
| POST | `/sessions/{id}/replay` | 重放历史会话，返回逐步 trace |
| GET  | `/reason-codes` | 返回全部原因码及含义 |
| GET  | `/healthz` | 健康检查 |

---

## 策略 AST 示例

### 节点类型（布尔表达式）

| op | 参数 | 含义 |
|----|------|------|
| `true` / `false` | — | 常量 |
| `and` / `or` | `children: Expr[]` | 短路之外做全量求值以保证 trace 稳定 |
| `not` | `child: Expr` | 非 |
| `age_gte` / `age_lt` | `n: int` | 年龄比较（0..150） |
| `timezone_in` | `zones: IANA[]` | 用户时区是否在集合内 |
| `local_time_between` | `start: "HH:MM"`, `end: "HH:MM"` | 用户本地时间在窗口内；start>end 表示跨零点窗口 |
| `daily_used_gte` | `seconds: int` | 今日已用秒数 ≥ 阈值 |
| `session_used_gte` | `seconds: int` | 本次会话已用秒数 ≥ 阈值 |
| `has_approval` | `approval: str` | 是否拥有指定类型的例外审批 |

### 效果（effect）

| kind | 参数 | 触发后的 reason |
|------|------|-----------------|
| `allow` | — | `ALLOWED` |
| `deny` | `reason_code`: 见下 | 指定的 DENIED_* |
| `limit_daily` | `seconds` | `LIMITED_DAILY_QUOTA_REACHED` |
| `limit_session` | `seconds` | `LIMITED_SESSION_QUOTA_REACHED` |

规则按顺序、**首次命中胜出**；无规则命中默认 `allow`。

### 示例：青少年默认策略

```json
{
  "name": "teen-default",
  "description": "短视频青少年保护默认策略",
  "rules": [
    {
      "id": "age-under-13",
      "when": {"op": "age_lt", "n": 13},
      "effect": {"kind": "deny", "reason_code": "DENIED_AGE_BELOW_MINIMUM"}
    },
    {
      "id": "teen-session-cap",
      "when": {"op": "and", "children": [
        {"op": "age_lt", "n": 18},
        {"op": "session_used_gte", "seconds": 900}
      ]},
      "effect": {"kind": "limit_session", "seconds": 900}
    },
    {
      "id": "teen-daily-cap",
      "when": {"op": "and", "children": [
        {"op": "age_lt", "n": 18},
        {"op": "daily_used_gte", "seconds": 3600}
      ]},
      "effect": {"kind": "limit_daily", "seconds": 3600}
    },
    {
      "id": "adult-daily-cap",
      "when": {"op": "and", "children": [
        {"op": "age_gte", "n": 18},
        {"op": "daily_used_gte", "seconds": 7200}
      ]},
      "effect": {"kind": "limit_daily", "seconds": 7200}
    },
    {
      "id": "bedtime",
      "when": {"op": "and", "children": [
        {"op": "age_lt", "n": 18},
        {"op": "local_time_between", "start": "22:00", "end": "06:00"},
        {"op": "not", "child": {"op": "has_approval", "approval": "parental-exception"}}
      ]},
      "effect": {"kind": "deny", "reason_code": "DENIED_BEDTIME_WINDOW"}
    },
    {
      "id": "allow-all",
      "when": {"op": "true"},
      "effect": {"kind": "allow"}
    }
  ]
}
```

### 预览请求示例

```bash
curl -s -X POST http://localhost:8000/api/v1/policies/preview \
  -H 'Content-Type: application/json' -H 'X-Tenant-Id: acme' \
  -d '{
    "document": <上面的 JSON>,
    "context": {
      "user_age": 10,
      "user_timezone": "Asia/Shanghai",
      "now_utc": "2026-06-01T10:00:00+00:00",
      "daily_used_seconds": 0,
      "session_used_seconds": 0,
      "approvals": []
    }
  }'
```

响应中的 `data.result.trace` 是按访问顺序排列的解释轨迹；对相同文档 + 相同 context 永远产生完全相同的 trace。

---

## 版本化与固定（Version Pinning）

* `POST /policies` 创建策略并发布版本 1；`POST /policies/{id}/versions` 发布新版本。每次发布把旧版本标记为非当前，新版本写入新一行，**旧版本行不可变**。
* `POST /sessions/start` 读取 `is_current=true` 的版本，**把完整 document 快照写入会话行**。之后所有心跳/结束/重放都从会话行读取该快照，不再依赖版本表——即使后续发布了更严格的版本，老会话也按旧策略结算（`VERSION_PINNED`）。
* 重放接口 `/sessions/{id}/replay` 用该快照 + outbox 事件流重新驱动状态机，用于对账与 bug 复盘。

## 幂等、并发与乱序

* 所有写接口都接受 `idempotency_key`：服务端把 `(tenant, key)` 映射到首次响应，后续重复请求直接返回缓存结果（`IDEMPOTENT_REPLAY`）。
* 心跳额外用客户端单调序号：`seq < last_seq` → `HEARTBEAT_OUT_OF_ORDER`；`seq == last_seq` → `HEARTBEAT_DUPLICATE`；时钟倒流 → `HEARTBEAT_STALE`。这些都是软拒绝，HTTP 200 返回当前状态。
* 同一租户下，同一用户同时最多一个 ACTIVE/PAUSED 会话，由数据库部分唯一索引 `ix_sessions_one_active_per_user` 保证；并发开始时第二名拿到 `CONCURRENT_SESSION_BLOCKED`（HTTP 409）。
* 暂停态不收心跳（`HEARTBEAT_PROHIBITED_WHEN_PAUSED`）；结束操作幂等（`SESSION_ALREADY_ENDED`）。

## 跨零点

心跳间隔按用户本地时区在零点处切分成多个 `(local_date, seconds)` 段，分别累加进 `daily_usage` 表，对应 `CROSS_MIDNIGHT_DAILY_RESET`。这保证每日限额在不同时区都准确。

## Outbox 事务发件箱

`sessions`/`policies`/`idempotency` 写入与 `outbox` 写入在同一个数据库事务中提交；后台 `OutboxDispatcher` 轮询未派发事件并交给注入的 `EventPublisher`（默认 `NullPublisher` 只打日志）。进程崩溃后未派发事件在下次启动时继续派发，业务状态与事件状态始终一致。

---

## 全部原因码含义

| Reason Code | 含义 |
|-------------|------|
| `OK` | 操作成功。 |
| `ALLOWED` | 策略允许该动作。 |
| `VERSION_PINNED` | 会话固定在启动时的策略版本上；新发布版本不影响它。 |
| `POLICY_CREATED` | 新策略（草稿）已创建。 |
| `POLICY_PUBLISHED` | 发布了不可变新版本；已有会话继续使用旧版本。 |
| `POLICY_PREVIEW_OK` | AST 已通过静态检查并在示例上下文上完成预览。 |
| `POLICY_VALID` | AST 通过所有静态检查（类型、矛盾、可达性）。 |
| `SESSION_STARTED` | 会话已创建并固定到当前发布版本。 |
| `SESSION_PAUSED` | 活动会话已暂停；恢复前不再累计时长。 |
| `SESSION_RESUMED` | 暂停的会话已恢复；继续累计时长。 |
| `SESSION_ENDED` | 会话已结束。结束是幂等的。 |
| `HEARTBEAT_ACCEPTED` | 心跳被接受，用量已累加。 |
| `USAGE_QUERIED` | 用量查询返回当前计数。 |
| `DENIED_AGE_BELOW_MINIMUM` | 用户年龄低于最小年龄要求。 |
| `DENIED_AGE_ABOVE_MAXIMUM` | 用户年龄高于允许的最大年龄。 |
| `DENIED_TIMEZONE_FORBIDDEN` | 用户时区在禁止集合内。 |
| `DENIED_BEDTIME_WINDOW` | 用户当前本地时间落在睡前禁刷窗口内。 |
| `DENIED_NO_APPROVAL` | 需要例外审批但用户未持有或已失效。 |
| `LIMITED_DAILY_QUOTA_REACHED` | 当日（用户本地日）累计已达上限。 |
| `LIMITED_SESSION_QUOTA_REACHED` | 本次会话累计已达上限。 |
| `IDEMPOTENT_REPLAY` | 命中幂等记录，返回首次响应。 |
| `HEARTBEAT_OUT_OF_ORDER` | 心跳序号早于已处理序号，忽略。 |
| `HEARTBEAT_DUPLICATE` | 相同序号的心跳已处理过，忽略。 |
| `HEARTBEAT_STALE` | 心跳时间过旧（时钟倒流），忽略。 |
| `SESSION_NOT_FOUND` | 本租户下未找到该会话。 |
| `SESSION_ALREADY_ENDED` | 会话已结束，操作为空操作。 |
| `SESSION_ALREADY_ACTIVE` | 会话已在活动中，重复开始被拒。 |
| `SESSION_NOT_ACTIVE` | 操作要求 ACTIVE 状态但实际不是。 |
| `SESSION_NOT_PAUSED` | 操作要求 PAUSED 状态但实际不是。 |
| `HEARTBEAT_PROHIBITED_WHEN_PAUSED` | 暂停期间不允许发心跳。 |
| `CONCURRENT_SESSION_BLOCKED` | 数据库唯一约束阻止了同用户的第二个活动/暂停会话。 |
| `TENANT_ISOLATION_VIOLATION` | 尝试访问其他租户的资源。 |
| `POLICY_NOT_FOUND` | 指定策略不存在。 |
| `POLICY_VERSION_NOT_FOUND` | 指定版本不存在。 |
| `POLICY_INVALID` | AST 未通过静态校验。 |
| `POLICY_AST_TYPE_ERROR` | AST 节点参数类型错误。 |
| `POLICY_AST_UNREACHABLE_RULE` | 规则被更早规则遮蔽，永远不会命中。 |
| `POLICY_AST_CONTRADICTION` | 策略中存在矛盾（如 min_age > max_age、空窗口）。 |
| `INVALID_TRANSITION` | 状态机不允许该状态转换。 |
| `CROSS_MIDNIGHT_DAILY_RESET` | 心跳跨越了用户本地零点，日用量桶已滚动。 |
| `REPLAY_COMPLETED` | 历史重放完成并产出确定性轨迹。 |

完整、权威的列表见 [reason_codes.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/reason_codes.py)，也可在运行时 `GET /api/v1/reason-codes` 获取。

---

## 设计要点（给 code reviewer）

* **领域层无 IO**：[domain](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain) 里除了 Pydantic 校验外不碰数据库；服务依赖 [repositories.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/repositories.py) 中的 `Protocol`，方便注入 in-memory 实现做单测。
* **可注入时钟/ID**：[clock.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/clock.py) 提供 `SystemClock` 与 `FakeClock`，[ids.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/ids.py) 提供 `UUID4IdGenerator` 与 `SequentialIdGenerator`，测试里的时间线完全可控。
* **AST 判别联合**：[policy_ast.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/policy_ast.py) 用 Pydantic v2 `Field(discriminator="op")` 做强类型，错误 JSON 在入口被拒绝。
* **静态检查**：[policy_validator.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/policy_validator.py) 用**有界模型枚举**检查规则可满足性与可达性（年龄边界、时区、时间窗口分钟边界、阈值秒数、审批子集）。
* **确定性 trace**：解释器对 `and/or` 节点做**全量求值**（不短路），使相同输入的 trace 长度与内容稳定；这是预览与历史重放的基础，见 [policy_interpreter.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/policy_interpreter.py)。
* **状态机**：[session.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/session.py) 的 `SessionAggregate` 纯函数式驱动 start/heartbeat/pause/resume/end，服务层负责持久化。
* **版本快照**：会话表保存 `policy_document` JSON 快照，发布新版本只是插入新行，旧会话不受影响。
* **并发**：并发开始依赖数据库部分唯一索引；服务端捕获 IntegrityError 转换为 `CONCURRENT_SESSION_BLOCKED`，见 [models.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/infra/db/models.py)。
* **Outbox**：[outbox.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/infra/db/outbox.py) 后台协程轮询派发，事务原子性保证“状态变化 + 事件”同时生效。
* **零停机迁移**：[0002_expand_policies_md.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/alembic/versions/0002_expand_policies_md.py) expand 加列，[0003_contract_policies_md.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/alembic/versions/0003_contract_policies_md.py) contract 收紧；测试在 [test_migrations.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/tests/test_migrations.py) 中覆盖升级、回滚与数据兼容。
* **历史重放**：[replay_service.py](file:///Users/huangding/Documents/GSB%203/0723/shortfeed-policy-engine-GSB-0723-Squirtle/spe/domain/services/replay_service.py) 从 outbox 事件流重建计数器，并用固定的 policy_document 快照重新评估，保证与线上决策一致。
