.PHONY: api-install api-test api-lint api-migrate api-run web-run

api-install:
	python3 -m venv apps/api/.venv
	apps/api/.venv/bin/python -m pip install -e packages/models
	apps/api/.venv/bin/python -m pip install -e apps/api
	apps/api/.venv/bin/python -m pip install -r apps/api/requirements.txt

api-test:
	apps/api/.venv/bin/python -m pytest apps/api/tests packages/models/tests

api-lint:
	apps/api/.venv/bin/python -m ruff check apps/api packages/models

api-migrate:
	test -f apps/api/.env
	cd apps/api && .venv/bin/alembic upgrade head

api-run: api-migrate
	test -f apps/api/.env
	cd apps/api && .venv/bin/uvicorn nexuspilot_api.main:app --app-dir src --reload

web-run:
	test -f apps/web/.env.local
	cd apps/web && npm run dev
