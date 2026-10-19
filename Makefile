.PHONY: install dev ui seed test lint eval openapi up down demo

install:
	uv sync
	cd ui && npm ci

dev:
	uv run uvicorn app.main:app --reload --port 8000

ui:
	cd ui && npm run build

seed:
	uv run python -m app.seed --reset

demo: seed eval

test:
	uv run pytest -q

lint:
	uv run ruff check app tests

eval:
	uv run python -m app.eval

openapi:
	uv run python -c "from app.main import export_openapi; export_openapi()"

up:
	docker compose up --build -d

down:
	docker compose down
