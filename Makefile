.PHONY: install dev migrate test lint format worker beat validate

install:
	python -m pip install -e '.[dev]'

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

migrate:
	alembic upgrade head

worker:
	celery -A app.workers.celery_app:celery_app worker --loglevel=INFO --queues=ai_interactive,ai_audio,ai_ingestion,ai_background

beat:
	celery -A app.workers.celery_app:celery_app beat --loglevel=INFO

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check .
	mypy .

format:
	ruff format .
	ruff check . --fix

validate:
	python scripts/validate_package.py
