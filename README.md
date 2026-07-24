# Short Video Session Policy Engine

多租户短视频会话策略引擎 — 提供版本化策略发布、规则预览、会话生命周期管理和用量查询。使用可校验的 JSON 策略 AST 表达年龄限制、时区感知、每日/单次限额、睡前禁刷和例外审批规则。

## 技术栈

- **Python 3.12** + **FastAPI** + **Pydantic v2**
- **SQLAlchemy 2** (async) + **asyncpg** + **PostgreSQL**
- **Alembic** 数据库迁移
- 分层架构：API → 领域 → 仓储 → Outbox

---

## 快速启动

### 1. 环境准备

```bash
# 创建虚拟环境
python3.12 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install fastapi "uvicorn[standard]" "pydantic&gt;=2.9.0" pydantic-settings \
    "sqlalchemy&gt;=2.0.35" asyncpg alembic python-dateutil pytz greenlet \
    pytest pytest-asyncio httpx pytest-cov

# 或使用 pyproject.toml
pip install -e ".[dev]"
```

### 2. 配置数据库

```bash
# 设置环境变量（或创建 .env 文件）
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy"
```

创建数据库：

```bash
createdb shortfeed_policy
```

### 3. 运行数据库迁移

```bash
# 执行迁移（初始化表结构）
alembic upgrade head
```

### 4. 启动服务

```bash
# 开发模式
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

服务启动后访问：
- API 文档：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc
- 健康检查：http://localhost:8000/api/v1/health

---

## 测试命令

```bash
# 运行所有测试
PYTHONPATH=. python -m pytest tests/ -v

# 运行单元测试（不需要数据库）
PYTHONPATH=. python -m pytest tests/test_policy_engine.py tests/test_session_aggregate.py -v

# 运行测试并生成覆盖率报告
PYTHONPATH=. python -m pytest tests/ --cov=app --cov-report=term-missing
```

---

## 迁移命令

```bash
# 升级到最新版本
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 查看当前版本
alembic current

# 查看迁移历史
alembic history

# 创建新迁移（自动生成）
alembic revision --autogenerate -m "description"

# 创建空迁移
alembic revision -m "description"
```

### 无停机迁移

初始迁移脚本采用以下无停机部署策略：

1. **新列带 DEFAULT 值** — 已有行自动填充，不锁表
2. **部分唯一索引** — 使用 PostgreSQL 部分索引 (`WHERE status IN ('active', 'paused')`) 防止双活动会话
3. **外键使用 CASCADE** — 删除租户自动清理关联数据
4. **JSONB 列** — PostgreSQL 原生 JSON 支持，可通过 GIN 索引查询

---

## 核心 API 端点

所有端点需要 `X-Tenant-Id` 请求头标识租户。写操作支持 `Idempotency-Key` 请求头实现幂等。

### 健康检查

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/v1/health` | 服务健康检查 |

### 策略管理

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/v1/policies` | 发布新版本策略 |
| GET | `/api/v1/policies` | 列出所有策略版本 |
| GET | `/api/v1/policies/{version}` | 获取指定版本策略 |
| POST | `/api/v1/policies/preview` | 预览策略评估（无需数据库） |
| POST | `/api/v1/policies/validate` | 静态校验策略 AST |

### 会话管理

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/v1/sessions` | 开始新会话 |
| POST | `/api/v1/sessions/{id}/heartbeat` | 发送心跳 |
| POST | `/api/v1/sessions/{id}/pause` | 暂停会话 |
| POST | `/api/v1/sessions/{id}/resume` | 恢复会话 |
| POST | `/api/v1/sessions/{id}/end` | 结束会话 |
| GET | `/api/v1/sessions/{id}` | 查询会话状态 |
| GET | `/api/v1/sessions/{id}/replay` | 历史重放（含评估轨迹） |
| GET | `/api/v1/sessions/usage/{user_id}` | 查询用户用量 |

### 请求示例

#### 发布策略

```bash
curl -X POST http://localhost:8000/api/v1/policies \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: default" \
  -d '{
    "name": "青少年保护策略",
    "description": "适用于13-17岁用户的限时策略",
    "ast": {
      "version": 1,
      "name": "青少年保护策略",
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
              "start_time": "22:00",
              "end_time": "06:00",
              "timezone": "Asia/Shanghai"
            }
          }
        ]
      }
    }
  }'
```

#### 开始会话

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: default" \
  -H "Idempotency-Key: start-user1-001" \
  -d '{
    "user_id": "user-001",
    "user_age": 15,
    "user_timezone": "Asia/Shanghai"
  }'
```

#### 发送心跳

```bash
curl -X POST http://localhost:8000/api/v1/sessions/{session_id}/heartbeat \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: default" \
  -d '{"sequence": 1}'
```

#### 查询用量

```bash
curl "http://localhost:8000/api/v1/sessions/usage/user-001?timezone=Asia/Shanghai" \
  -H "X-Tenant-Id: default"
```

---

## 策略 AST 示例

### 节点类型

| type | 说明 | 关键字段 |
|------|------|----------|
| `age_gate` | 年龄门槛 | `min_age` |
| `daily_limit` | 每日时长限额 | `max_minutes_per_day`, `timezone` |
| `session_limit` | 单次会话限额 | `max_minutes_per_session` |
| `bedtime_ban` | 睡前禁刷时段 | `start_time`, `end_time`, `timezone` |
| `time_window` | 允许使用时间窗 | `start_time`, `end_time`, `timezone`, `days_of_week` |
| `exception` | 例外审批白名单 | `user_ids`, `reason`, `valid_until` |
| `and` | 逻辑与 | `rules[]` |
| `or` | 逻辑或 | `rules[]` |
| `not` | 逻辑非 | `rule` |

### 示例 1：基础青少年保护

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
          "start_time": "22:00",
          "end_time": "06:00",
          "timezone": "Asia/Shanghai"
        }
      }
    ]
  }
}
```

### 示例 2：VIP 例外 + 时间窗

```json
{
  "version": 1,
  "name": "VIP 策略",
  "rule": {
    "type": "and",
    "rules": [
      {"type": "age_gate", "min_age": 18},
      {
        "type": "or",
        "rules": [
          {"type": "exception", "user_ids": ["vip-001", "vip-002"], "reason": "付费会员"},
          {
            "type": "and",
            "rules": [
              {"type": "daily_limit", "max_minutes_per_day": 60, "timezone": "UTC"},
              {
                "type": "time_window",
                "start_time": "08:00",
                "end_time": "22:00",
                "timezone": "UTC",
                "days_of_week": [0, 1, 2, 3, 4]
              }
            ]
          }
        ]
      }
    ]
  }
}
```

### 示例 3：仅周末放宽限制

```json
{
  "version": 1,
  "name": "周末策略",
  "rule": {
    "type": "and",
    "rules": [
      {"type": "age_gate", "min_age": 13},
      {
        "type": "or",
        "rules": [
          {
            "type": "and",
            "rules": [
              {"type": "daily_limit", "max_minutes_per_day": 60},
              {
                "type": "time_window",
                "start_time": "08:00",
                "end_time": "21:00",
                "days_of_week": [0, 1, 2, 3, 4]
              }
            ]
          },
          {
            "type": "and",
            "rules": [
              {"type": "daily_limit", "max_minutes_per_day": 180},
              {
                "type": "time_window",
                "start_time": "09:00",
                "end_time": "22:00",
                "days_of_week": [5, 6]
              }
            ]
          }
        ]
      }
    ]
  }
}
```

---

## 架构概览

```
┌─────────────────────────────────────────┐
│              API Layer                   │
│  FastAPI Routes + Pydantic Schemas       │
│  (app/api/v1/)                           │
├─────────────────────────────────────────┤
│             Service Layer                │
│  PolicyService / SessionService          │
│  (app/services/)                         │
├─────────────────────────────────────────┤
│              Domain Layer                │
│  Policy AST / Engine / Session Aggregate │
│  Enums / Events / Checker                │
│  (app/domain/)                           │
├─────────────────────────────────────────┤
│           Repository Layer               │
│  PolicyRepo / SessionRepo / OutboxRepo   │
│  (app/infrastructure/repositories/)      │
├─────────────────────────────────────────┤
│         Infrastructure Layer             │
│  SQLAlchemy Models / DB Session          │
│  Clock / IdGenerator (injectable)        │
│  (app/infrastructure/)                   │
└─────────────────────────────────────────┘
```

### 关键设计决策

1. **策略版本固定**：每个会话启动时绑定当时的策略版本和策略 ID，新版本发布不影响已有会话的结算
2. **数据库约束防并发**：PostgreSQL 部分唯一索引 `(tenant_id, user_id) WHERE status IN ('active', 'paused')` 确保同一用户不会同时有两个活动会话
3. **心跳幂等**：通过 `last_heartbeat_seq` 追踪，重复或乱序心跳被安全拒绝
4. **Outbox 模式**：领域事件通过事务性发件箱保证可靠投递
5. **可注入时间/ID**：`Clock` 协议和 `IdGenerator` 协议支持测试时注入固定时钟和顺序 ID
6. **时区感知**：每日限额和睡前禁刷均使用用户时区计算日界线
7. **跨午夜处理**：按用户时区计算自然日，用量统计自动按日分界
8. **进程重启恢复**：所有会话状态持久化在数据库，重启后从 DB 恢复
9. **确定性重放**：基于不可变策略 AST + 事件日志，可完全重放任意历史会话的评估轨迹

---

## 全部原因码含义

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
| `exception_approved` | 用户拥有经审批的例外权限，规则被覆盖 |
| `session_not_found` | 请求的会话不存在 |
| `session_already_active` | 该用户已有一个活动会话 |
| `session_not_active` | 会话当前不处于活动状态 |
| `session_already_ended` | 会话已结束，无法执行操作 |
| `session_already_paused` | 会话已处于暂停状态 |
| `session_not_paused` | 会话未暂停，无法执行恢复 |
| `duplicate_heartbeat` | 相同序号的心跳已处理过（幂等返回） |
| `heartbeat_out_of_order` | 心跳序号小于最后处理的序号（乱序拒绝） |
| `idempotency_conflict` | Idempotency-Key 与之前不同请求冲突 |
| `tenant_mismatch` | 资源属于其他租户（租户隔离违规） |
| `invalid_policy_ast` | 策略 AST 语法结构无效 |
| `policy_version_pinned` | 会话绑定到特定策略版本，新版本不影响该会话 |
| `concurrent_session_blocked` | 数据库约束阻止了并发活动会话的创建 |
| `cross_midnight_reset` | 跨午夜时每日用量计数器已重置 |
| `session_expired` | 会话因策略强制过期（如达到限额） |

---

## 项目结构

```
shortfeed-policy-engine/
├── app/
│   ├── main.py                    # FastAPI 应用入口
│   ├── config.py                  # 配置管理
│   ├── api/
│   │   ├── deps.py                # 依赖注入
│   │   └── v1/
│   │       ├── health.py          # 健康检查端点
│   │       ├── policies.py        # 策略管理端点
│   │       └── sessions.py        # 会话管理端点
│   ├── domain/
│   │   ├── enums.py               # 原因码、状态、动作枚举
│   │   ├── policy/
│   │   │   ├── ast.py             # 策略 AST Pydantic 模型
│   │   │   ├── checker.py         # 策略静态校验器
│   │   │   └── engine.py          # 策略评估引擎（含解释轨迹）
│   │   └── session/
│   │       ├── aggregate.py       # 会话聚合根
│   │       └── events.py          # 领域事件
│   ├── services/
│   │   ├── policy_service.py      # 策略应用服务
│   │   └── session_service.py     # 会话应用服务
│   ├── schemas/
│   │   ├── common.py              # 通用响应模型
│   │   ├── policy.py              # 策略请求/响应模型
│   │   └── session.py             # 会话请求/响应模型
│   └── infrastructure/
│       ├── clock.py               # 可注入时钟
│       ├── id_generator.py        # 可注入 ID 生成器
│       ├── db/
│       │   ├── base.py            # SQLAlchemy 基类
│       │   ├── session.py         # 异步数据库会话
│       │   └── models/            # ORM 模型
│       └── repositories/          # 数据仓储
├── alembic/
│   ├── env.py                     # Alembic 环境配置
│   └── versions/
│       └── 001_initial_schema.py  # 初始迁移脚本
├── tests/
│   ├── test_policy_engine.py      # 策略引擎单元测试（29个）
│   └── test_session_aggregate.py  # 会话聚合单元测试（12个）
├── pyproject.toml
├── alembic.ini
└── README.md
```
