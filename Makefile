.PHONY: install clean load-graph api frontend test lint format reset-graph all

install:
	pip install -r requirements.txt

clean:
	python scripts/clean_data.py

load-graph:
	python scripts/load_neo4j.py

reset-graph:
	python scripts/load_neo4j.py --reset

api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	streamlit run app/frontend/streamlit_app.py

test:
	pytest -v

lint:
	ruff check .

format:
	ruff format .

# Full local bootstrap: clean data -> load into Neo4j -> run tests
all: clean load-graph test
