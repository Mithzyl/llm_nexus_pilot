.PHONY: api-install api-test api-lint api-migrate api-run

api-install:
	python3 -m venv apps/api/.venv
	apps/api/.venv/bin/python -m pip install -r apps/api/requirements.txt

api-test:
	apps/api/.venv/bin/python -m pytest apps/api/tests

api-lint:
	apps/api/.venv/bin/python -m ruff check apps/api

api-migrate:
	cd apps/api && .venv/bin/alembic upgrade head

api-run:
	cd apps/api && .venv/bin/uvicorn nexuspilot_api.main:app --reload

