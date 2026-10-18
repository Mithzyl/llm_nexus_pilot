FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
WORKDIR /app

# PDF/Office dependencies live only in the dedicated file-runtime image.
RUN groupadd --gid 10001 nexus && useradd --uid 10001 --gid nexus --no-create-home nexus

COPY apps/api/requirements.txt ./apps/api/requirements.txt
RUN python -m pip install --no-cache-dir -r apps/api/requirements.txt
RUN python -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")' \
    && chmod -R a+rX /opt/tiktoken-cache
COPY packages/models ./packages/models
COPY apps/api/pyproject.toml apps/api/alembic.ini ./apps/api/
COPY apps/api/migrations ./apps/api/migrations
COPY apps/api/src ./apps/api/src
RUN python -m pip install --no-cache-dir --no-deps ./packages/models ./apps/api
COPY deployments/production/bootstrap_minio.py /app/deployments/production/bootstrap_minio.py
WORKDIR /app/apps/api
USER 10001:10001

CMD ["uvicorn", "nexuspilot_api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
