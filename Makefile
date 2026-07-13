.PHONY: install test api

install:
	pip install -r requirements.txt

api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q
