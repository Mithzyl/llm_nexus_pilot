FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY packages/models ./packages/models
COPY apps/api/requirements.txt ./apps/api/requirements.txt
COPY apps/api/pyproject.toml apps/api/alembic.ini ./apps/api/
COPY apps/api/migrations ./apps/api/migrations
COPY apps/api/src ./apps/api/src
RUN python -m pip install --no-cache-dir ./packages/models ./apps/api -r apps/api/requirements.txt
WORKDIR /app/apps/api

CMD ["uvicorn", "nexuspilot_api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
