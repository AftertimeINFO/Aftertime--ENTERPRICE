.PHONY: up down build logs test sync reset export-models check-models

up:
	docker compose up --build -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f api worker frontend

test:
	docker compose run --rm api python -m pytest

sync:
	docker compose exec api python -m app.sync_cli --source chebi --external-id 16134

reset:
	docker compose down -v

export-models:
	python tools/models.py export

check-models:
	python tools/models.py check
