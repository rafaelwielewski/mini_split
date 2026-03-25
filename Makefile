.PHONY: install dev migrate test lint format

install:
	poetry install

dev:
	python manage.py runserver

migrate:
	python manage.py migrate

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check .

format:
	ruff format .
