FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    'fastapi>=0.115.0' \
    'uvicorn[standard]>=0.32.0' \
    'pydantic>=2.9.0' \
    'pydantic-settings>=2.5.0' \
    'sqlalchemy>=2.0.35' \
    'asyncpg>=0.29.0' \
    'alembic>=1.13.0' \
    'python-dateutil>=2.9.0' \
    'pytz>=2024.2' \
    'greenlet'

COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
