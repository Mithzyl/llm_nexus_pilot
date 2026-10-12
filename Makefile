.PHONY: api-install api-test api-lint api-migrate api-run web-run web-test web-check web-e2e file-runtime-build file-runtime-check task-runtime-build task-runtime-test

# Both local processes use one explicit socket; no credentials are passed to the executor.
FILE_RUNTIME_SOCKET ?= /tmp/nexuspilot-file-runtime-$(shell id -u)/executor.sock
FILE_RUNTIME_IMAGE ?= nexuspilot-file-runtime:local
.PHONY: file-runtime-run api-run-with-files

file-runtime-run:
	python3 deployments/file-runtime/serve_runtime.py --create-directory --socket "$(FILE_RUNTIME_SOCKET)" --image "$(FILE_RUNTIME_IMAGE)"

api-run-with-files: api-migrate
	cd apps/api && NEXUSPILOT_FILE_RUNTIME_SOCKET="$(FILE_RUNTIME_SOCKET)" .venv/bin/uvicorn nexuspilot_api.main:app --app-dir src --reload

file-runtime-build:
	python3 deployments/file-runtime/manage_runtime.py build

file-runtime-check:
	python3 deployments/file-runtime/manage_runtime.py check

task-runtime-build:
	python3 deployments/task-runtime/task_runtime.py --build-image

task-runtime-test:
	NEXUSPILOT_TEST_TASK_RUNTIME=1 apps/api/.venv/bin/pytest -c apps/api/pyproject.toml apps/api/tests/test_task_runtime.py -q

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

web-test:
	npm --prefix apps/web test

web-check:
	npm --prefix apps/web run typecheck
	npm --prefix apps/web run lint
	npm --prefix apps/web run build

web-e2e:
	npm --prefix apps/web run test:e2e
