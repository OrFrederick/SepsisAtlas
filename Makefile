.PHONY: install initdb parse ingest extract validate serve up down test fmt kg-shell kg-inspect

install:
	pip install -e ".[dev]"

initdb:
	python -m sepsis_atlas.db

parse:
	python -m parse.run_parse

extract:
	python -m extract.run_extract --gt-only

ingest: parse extract

validate:
	python scripts/validate.py

serve:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

up:
	docker compose up --build

down:
	docker compose down

test:
	pytest -q

fmt:
	ruff format src/ scripts/ tests/
	ruff check --fix src/ scripts/ tests/

kg-shell:
	docker exec -it sepsis_neo4j cypher-shell -u neo4j -p sepsisatlas

kg-inspect:
	uv run python -m scripts.kg_inspect
