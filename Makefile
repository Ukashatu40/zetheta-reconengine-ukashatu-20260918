# Makefile
# Wraps the exact, correct connection details for every recurring command,
# so passwords and hostnames are never hand-typed or copy-pasted again.
#
# Two hostnames matter and are never interchangeable:
#   - "postgres"  resolves only inside the Docker Compose network
#                 (used by targets that run via `docker compose exec`)
#   - "localhost" resolves only from the host shell, via the published port
#                 (used by targets that run pytest/alembic directly on host)
#
# NOTE: Makefile recipe lines must be indented with a literal TAB character,
# not spaces. If `make` reports "missing separator", re-indent with Tab.

POSTGRES_PASSWORD := $(shell grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2)

TEST_DB_URL := postgresql+psycopg://recon_app:$(POSTGRES_PASSWORD)@localhost:5432/recon_test

.PHONY: up down build migrate migrate-test psql psql-test \
        fmt lint typecheck test test-integration check api-key \
        golden golden-manifest seed bench coverage \
        web-install web-dev web-check api-types

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up -d --build

## Applies migrations to the main app database. Runs inside the api
## container, so "postgres" resolves correctly — no host/localhost
## confusion possible.
migrate:
	docker compose exec api alembic upgrade head

## Creates (idempotently) and migrates the test-only database. Still runs
## inside the api container via exec, with only the dbname overridden —
## this avoids the host DNS problem entirely, for both creation and
## migration.
migrate-test:
	docker compose exec postgres psql -U recon_app -d recon \
		-c "CREATE DATABASE recon_test OWNER recon_app;" || true
	docker compose exec \
		-e DATABASE_URL="postgresql+psycopg://recon_app:$(POSTGRES_PASSWORD)@postgres:5432/recon_test" \
		api alembic upgrade head

api-key:
	docker compose exec api python -m recon.cli.create_api_key --name $(NAME) --role $(ROLE)

psql:
	docker compose exec postgres psql -U recon_app -d recon

psql-test:
	docker compose exec postgres psql -U recon_app -d recon_test

fmt:
	black .

lint:
	ruff check .

typecheck:
	mypy

test:
	pytest -q

## Runs from HOST (pytest has to), so this is the one place "localhost"
## is correct rather than "postgres".
test-integration:
	TEST_DATABASE_URL="postgresql+psycopg://recon_app:$(POSTGRES_PASSWORD)@localhost:5432/recon_test" \
		pytest -q -m integration -v

check: fmt lint typecheck test

## The golden gate (A8.3): regenerates the planted dataset, runs it through the real
## pipeline and fails if any scenario's outcome differs from data/golden/manifest.json.
golden:
	TEST_DATABASE_URL="$(TEST_DB_URL)" pytest -q -m golden

## Rewrite the committed manifest after an intended change to the generator.
golden-manifest:
	python -m recon.cli.golden --write-manifest

## Demo data through the real pipeline (inside the api container). SCALE multiplies volume.
seed:
	docker compose exec api python -m recon.cli.seed --scale $(or $(SCALE),1) --weeks $(or $(WEEKS),1)

## Pipeline benchmark against the A6.1 targets; results land in docs/benchmarks/pipeline/.
bench:
	TEST_DATABASE_URL="$(TEST_DB_URL)" python -m recon.cli.benchmark --scale $(or $(SCALE),20) \
		--output docs/benchmarks/pipeline/scale-$(or $(SCALE),20).json

coverage:
	TEST_DATABASE_URL="$(TEST_DB_URL)" pytest -q --cov=recon --cov-report=term-missing --cov-fail-under=80

## The web dashboard (frontend/). `web-dev` proxies /api to the engine on localhost:8000.
web-install:
	cd frontend && npm ci

web-dev:
	cd frontend && npm run dev

web-check:
	cd frontend && npm run check && npm run build

## Regenerate the dashboard's API types after a backend change.
api-types:
	python scripts/export_openapi.py
	cd frontend && npm run gen:api
