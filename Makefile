.PHONY: install lint fmt type test cov check

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
