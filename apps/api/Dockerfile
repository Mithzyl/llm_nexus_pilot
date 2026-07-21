FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY src ./src

CMD ["uvicorn", "nexuspilot_api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]

