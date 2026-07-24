.PHONY: setup db-up db-down migrate run worker test test-integration lint

PYTHON = .venv/bin/python
PIP = .venv/bin/pip
PYTEST = .venv/bin/pytest
ALEMBIC = .venv/bin/alembic
UVICORN = .venv/bin/uvicorn

setup:
	python3.12 -m venv .venv
	$(PIP) install fastapi "uvicorn[standard]" "pydantic>=2.9.0" pydantic-settings \
		"sqlalchemy>=2.0.35" asyncpg alembic python-dateutil pytz greenlet \
		pytest pytest-asyncio httpx pytest-cov

db-up:
	docker compose up -d

db-down:
	docker compose down

db-create:
	PGPASSWORD=postgres psql -h localhost -U postgres -c "CREATE DATABASE shortfeed_policy;" 2>/dev/null || true
	PGPASSWORD=postgres psql -h localhost -U postgres -c "CREATE DATABASE shortfeed_policy_test;" 2>/dev/null || true

migrate:
	$(ALEMBIC) upgrade head

migrate-down:
	$(ALEMBIC) downgrade -1

run:
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m app.worker

test:
	PYTHONPATH=. $(PYTEST) tests/test_policy_engine.py tests/test_session_aggregate.py tests/test_time_splitting.py -v

test-integration:
	TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy_test \
	PYTHONPATH=. $(PYTEST) tests/ -v

test-all:
	TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/shortfeed_policy_test \
	PYTHONPATH=. $(PYTEST) tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
