.PHONY: install lint fmt type test cov check demo demo-seed demo-eval demo-down

DEMO_DB_PORT ?= 55432
DEMO_PORT ?= 8001
DEMO_DSN = postgresql://asknews:asknews@127.0.0.1:$(DEMO_DB_PORT)/asknews_demo
DEMO_COMPOSE = ASKNEWS_DEMO_DB_PORT=$(DEMO_DB_PORT) docker compose -p asknews-demo -f compose.demo.yml

install:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

type:
	uv run mypy

test:
	uv run pytest

cov:
	uv run pytest --cov --cov-report=term --cov-report=xml

check: lint type test

demo-seed:
	$(DEMO_COMPOSE) up -d --wait
	uv run asknews demo-seed --dsn '$(DEMO_DSN)'

demo: demo-seed
	DATABASE_URL='$(DEMO_DSN)' ASKNEWS_EMBEDDER=hash ASKNEWS_LLM=scripted \
	  ASKNEWS_DOMAIN_DIR=domain ASKNEWS_SITE_URL=http://127.0.0.1:$(DEMO_PORT) \
	  uv run uvicorn asknews.api.app:app --host 127.0.0.1 --port $(DEMO_PORT)

demo-eval: demo-seed
	DATABASE_URL='$(DEMO_DSN)' ASKNEWS_EMBEDDER=hash ASKNEWS_DOMAIN_DIR=domain \
	  uv run asknews eval --cases domain/demo_eval.yaml --as-of 2025-10-09 \
	  --corpus-label demo-v1 --report data/eval/demo.json

demo-down:
	$(DEMO_COMPOSE) down
