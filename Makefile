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

.PHONY: up down build migrate migrate-test psql psql-test \
        fmt lint typecheck test test-integration check

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
