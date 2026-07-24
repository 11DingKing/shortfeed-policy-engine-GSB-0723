# Short Video Session Policy Engine

多租户短视频会话策略引擎 — 提供版本化策略发布、规则预览、会话生命周期管理和用量查询。使用可校验的 JSON 策略 AST 表达年龄限制、时区感知、每日/单次限额、睡前禁刷和例外审批规则。

## 技术栈

- **Python 3.12** + **FastAPI** + **Pydantic v2**
- **SQLAlchemy 2** (async) + **asyncpg** + **PostgreSQL 16**
- **Alembic** 数据库迁移（支持无停机部署）
- **Outbox Pattern** 事务性事件发布 Worker
- 分层架构：API → 服务 → 领域 → 仓储 → Outbox

---

## 一键启动（使用 Docker Compose）

```bash
# 1. 启动 PostgreSQL
docker compose up -d

# 2. 设置环境
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" "pydantic>=2.9.0" pydantic-settings \
    "sqlalchemy>=2.0.35" asyncpg alembic python-dateutil pytz greenlet \
    pytest pytest-asyncio httpx pytest-cov

# 3. 创建数据库并迁移
PGPASSWORD=postgres psql -h localhost -U postgres -c "CREATE DATABASE shortfeed_policy;"
alembic upgrade head

# 4. 启动 API 服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 5. (另一个终端) 启动 Outbox Worker
python -m app.worker
```

或使用 Makefile：

```bash
make setup          # 创建 venv 并安装依赖
make db-up          # docker compose up -d
make db-create      # 创建数据库
make migrate        # alembic upgrade head
make run            # 启动 API (开发模式)
make worker         # 启动 Outbox Worker
make test           # 运行单元测试 (无需数据库)
make test-all       # 运行全部测试 (需要数据库)
make db-down        # 停止 PostgreSQL
```

服务启动后访问：
- API 文档（Swagger UI）：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc
- 健康检查：http://localhost:8000/api/v1/health

---

## 测试

```bash
# 单元测试（无需数据库，49 个测试）
PYTHONPATH=. python -m pytest tests/test_policy_engine.py tests/test_session_aggregate.py tests/test_time_splitting.py -v

# 集成测试（需要 PostgreSQL，12 个测试）
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy_test \
  PYTHONPATH=. python -m pytest tests/test_integration.py -v

# 全部测试 (61 个)
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy_test \
  PYTHONPATH=. python -m pytest tests/ -v
```

---

## 迁移命令

```bash
alembic upgrade head       # 升级到最新版本
alembic downgrade -1      # 回滚一个版本
alembic current           # 查看当前版本
alembic history           # 查看迁移历史
```

### 无停机迁移策略

- 新列带 `DEFAULT` 值，已有行自动填充，不锁表
- 部分唯一索引 `(tenant_id, user_id) WHERE status IN ('active', 'paused')` 防止双活动会话
- 外键使用 `ON DELETE CASCADE`
- PostgreSQL `JSONB` + `ON CONFLICT DO UPDATE` (upsert) 用于每日用量原子累加

---

## 核心 API 端点

所有端点需要 `X-Tenant-Id` 请求头。写操作支持 `Idempotency-Key` 头实现幂等。

| Method | Path | 说明 |
|--------|------|------|
| **健康检查** | | |
| GET | `/api/v1/health` | 服务健康检查 |
| **策略管理** | | |
| POST | `/api/v1/policies` | 发布新版本策略 |
| GET | `/api/v1/policies` | 列出所有策略版本 |
| GET | `/api/v1/policies/{version}` | 获取指定版本策略 |
| POST | `/api/v1/policies/preview` | 预览策略评估（无需数据库） |
| POST | `/api/v1/policies/validate` | 静态校验策略 AST |
| **会话管理** | | |
| POST | `/api/v1/sessions` | 开始新会话 |
| POST | `/api/v1/sessions/{id}/heartbeat` | 发送心跳 |
| POST | `/api/v1/sessions/{id}/pause` | 暂停会话 |
| POST | `/api/v1/sessions/{id}/resume` | 恢复会话 |
| POST | `/api/v1/sessions/{id}/end` | 结束会话 |
| GET | `/api/v1/sessions/{id}` | 查询会话状态 |
| GET | `/api/v1/sessions/{id}/replay` | 历史重放（含评估轨迹） |
| GET | `/api/v1/sessions/usage/{user_id}` | 查询用户用量 |

### cURL 示例

```bash
# 发布策略
curl -X POST http://localhost:8000/api/v1/policies \
  -H "Content-Type: application/json" -H "X-Tenant-Id: default" \
  -d '{"name":"test","ast":{"version":1,"name":"test","rule":{"type":"and","rules":[{"type":"age_gate","min_age":13},{"type":"daily_limit","max_minutes_per_day":120,"timezone":"UTC"}]}}}'

# 开始会话
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" -H "X-Tenant-Id: default" -H "Idempotency-Key: key-001" \
  -d '{"user_id":"user-1","user_age":20}'

# 发送心跳
curl -X POST http://localhost:8000/api/v1/sessions/{session_id}/heartbeat \
  -H "Content-Type: application/json" -H "X-Tenant-Id: default" \
  -d '{"sequence": 1}'

# 查询用量
curl "http://localhost:8000/api/v1/sessions/usage/user-1?timezone=UTC" \
  -H "X-Tenant-Id: default"
```

---

## 策略 AST

### 节点类型

| type | 说明 | 关键字段 |
|------|------|----------|
| `age_gate` | 年龄门槛 | `min_age` |
| `daily_limit` | 每日时长限额 | `max_minutes_per_day`, `timezone` |
| `session_limit` | 单次会话限额 | `max_minutes_per_session` |
| `bedtime_ban` | 睡前禁刷 | `start_time`, `end_time`, `timezone` |
| `time_window` | 允许使用时间窗 | `start_time`, `end_time`, `timezone`, `days_of_week` |
| `exception` | 例外审批白名单 | `user_ids`, `reason`, `valid_until` |
| `and` | 逻辑与 | `rules[]` |
| `or` | 逻辑或 | `rules[]` |
| `not` | 逻辑非 | `rule` |

### 示例：青少年保护策略

```json
{
  "version": 1,
  "name": "青少年保护",
  "rule": {
    "type": "and",
    "rules": [
      {"type": "age_gate", "min_age": 13},
      {"type": "daily_limit", "max_minutes_per_day": 120, "timezone": "Asia/Shanghai"},
      {"type": "session_limit", "max_minutes_per_session": 30},
      {
        "type": "not",
        "rule": {
          "type": "bedtime_ban",
          "start_time": "22:00", "end_time": "06:00",
          "timezone": "Asia/Shanghai"
        }
      }
    ]
  }
}
```

---

## 关键设计与一致性保障

### 心跳正确性修复
- **问题**：原实现在调用聚合根 `heartbeat()` 前预先更新 `last_heartbeat_seq`，导致合法心跳被误判为重复且不生成领域事件
- **修复**：使用 `SELECT ... FOR UPDATE` 行锁读取会话 → 创建聚合根 → 聚合根校验并更新状态 → 回写 DB。聚合根是唯一的序号判断者

### 并发安全
- **行级锁**：所有写操作（heartbeat/pause/resume/end）使用 `SELECT FOR UPDATE` 锁定会话行
- **数据库约束**：PostgreSQL 部分唯一索引防止同一用户并发创建双活动会话
- **原子 UPSERT**：每日用量使用 `INSERT ... ON CONFLICT DO UPDATE` 原子累加

### 跨午夜时长拆分
- 心跳/pause/end 时，计算从上一次心跳到当前时间的时长
- 使用 `split_seconds_by_local_day()` 按用户本地时区将时长精确拆分到每个自然日
- 拆分结果通过 `daily_usage` 表的 upsert 原子累加
- 每日限额判断基于 `daily_usage` 表（累计同一用户本地日期内所有会话）

### 历史重放
- 重放时使用事件日志中的 delta_seconds 重建会话活跃时间
- 按相同的跨午夜拆分逻辑计算当时的每日用量
- `daily_used_minutes_at_time` 和 `session_active_seconds_at_time` 反映事件发生时的真实上下文

### Outbox Worker
- `python -m app.worker` 启动后台 Worker
- 使用 `SELECT FOR UPDATE SKIP LOCKED` 批量获取未处理消息
- 支持优雅关闭（SIGINT/SIGTERM）、重试计数和错误记录
- 默认发布到日志（可扩展为消息队列）

### 其他保障
- **策略版本固定**：会话绑定策略 ID + version，新版本不影响历史结算
- **租户隔离**：所有查询携带 `tenant_id`，`X-Tenant-Id` 头必填
- **幂等键**：`Idempotency-Key` 头防止重复创建会话
- **进程重启恢复**：所有状态持久化在 PostgreSQL
- **可注入时钟/ID**：`FixedClock` + `SequentialGenerator` 支持确定性测试

---

## 全部原因码

| Reason Code | 含义 |
|-------------|------|
| `ok` | 操作成功完成 |
| `policy_not_found` | 指定版本的策略在该租户中不存在 |
| `policy_validation_failed` | 策略 AST 未通过静态校验 |
| `age_gate_blocked` | 用户未达到最低年龄要求 |
| `daily_limit_exceeded` | 用户已达到每日使用时长上限 |
| `session_limit_exceeded` | 单次会话时长已达上限 |
| `bedtime_ban_active` | 当前时间处于睡前禁刷时段 |
| `not_in_time_window` | 当前时间不在允许使用的时间窗口内 |
| `exception_approved` | 用户拥有经审批的例外权限 |
| `session_not_found` | 请求的会话不存在 |
| `session_already_active` | 该用户已有一个活动会话 |
| `session_not_active` | 会话当前不处于活动状态 |
| `session_already_ended` | 会话已结束 |
| `session_already_paused` | 会话已处于暂停状态 |
| `session_not_paused` | 会话未暂停，无法恢复 |
| `duplicate_heartbeat` | 相同序号心跳已处理（幂等返回） |
| `heartbeat_out_of_order` | 心跳序号小于已处理序号（乱序拒绝） |
| `idempotency_conflict` | Idempotency-Key 与不同请求冲突 |
| `tenant_mismatch` | 资源属于其他租户 |
| `invalid_policy_ast` | 策略 AST 语法无效 |
| `policy_version_pinned` | 会话绑定到特定策略版本 |
| `concurrent_session_blocked` | 数据库约束阻止并发会话 |
| `cross_midnight_reset` | 跨午夜时每日计数器已重置 |
| `session_expired` | 会话因策略强制过期 |

---

## 项目结构

```
app/
├── main.py                     # FastAPI 入口
├── config.py                   # 配置 (Pydantic Settings)
├── worker.py                   # Outbox 发布 Worker
├── api/
│   ├── deps.py                 # 依赖注入
│   └── v1/
│       ├── health.py
│       ├── policies.py
│       └── sessions.py
├── domain/
│   ├── enums.py                # ReasonCode / SessionStatus / SessionAction
│   ├── policy/
│   │   ├── ast.py              # Pydantic AST 模型 (判别联合)
│   │   ├── checker.py          # 静态校验
│   │   └── engine.py           # 评估引擎 + TraceNode 解释轨迹
│   └── session/
│       ├── aggregate.py        # SessionAggregate 状态机
│       └── events.py           # DomainEvent + EventRecorder
├── services/
│   ├── policy_service.py
│   └── session_service.py      # 含跨午夜拆分、SELECT FOR UPDATE
├── schemas/                    # Pydantic 请求/响应模型
└── infrastructure/
    ├── clock.py                # Clock Protocol (SystemClock/FixedClock)
    ├── id_generator.py         # IdGenerator Protocol (Uuid4/Sequential)
    ├── db/
    │   ├── base.py
    │   ├── session.py          # AsyncSession 工厂
    │   └── models/             # SQLAlchemy 2 ORM 模型
    └── repositories/           # 数据仓储 + Outbox
tests/
├── conftest.py                 # PostgreSQL 集成测试配置
├── test_policy_engine.py       # 策略引擎测试 (29)
├── test_session_aggregate.py   # 会话聚合测试 (12)
├── test_time_splitting.py      # 跨午夜拆分测试 (8)
└── test_integration.py         # 端到端集成测试 (12)
```
