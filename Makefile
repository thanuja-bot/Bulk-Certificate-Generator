.PHONY: install run test lint format format-check typecheck migrate seed ci

install:
	python -m pip install -r requirements.txt

run:
	uvicorn app.main:app --host 0.0.0.0 --port $${PORT:-5000} --reload

test:
	python -m pytest

lint:
	ruff check .

format:
	ruff format .

format-check:
	ruff format --check .

typecheck:
	mypy --strict app/

migrate:
	alembic upgrade head

seed:
	python scripts/seed_demo.py

ci: lint format-check typecheck test
