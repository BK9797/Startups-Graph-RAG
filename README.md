# 🕸️ Tech / Startups GraphRAG

A small GraphRAG app for querying a Neo4j knowledge graph of startups,
founders, investors, products, and awards. The app uses embedding and
fulltext similarity search to retrieve relevant entities, then asks Groq
to synthesize a grounded answer from the retrieved graph context.

## What it does

- Accepts natural-language questions through a FastAPI API
- Retrieves relevant entities with embedding and fulltext similarity search
- Builds a lightweight graph context from the matched entities
- Synthesizes answers with Groq from the retrieved graph context

## Quick start

```bash
cd Startups-Graph-RAG 
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set at least:

- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`
- `GROQ_API_KEY`

Then run:

```bash
make test
make api
```

Open `http://localhost:8000/docs` for the API docs.

## Project structure

- `app/main.py` — FastAPI entrypoint
- `app/api/routes.py` — API routes for health and answering
- `app/core/embedding.py` — deterministic embedding-based similarity matching
- `app/core/embedding.py` — embedding-based fallback matching
- `app/core/graph_rag.py` — orchestration layer
- `app/core/llm.py` — Groq answer generation wrapper
- `app/db/neo4j_client.py` — Neo4j connection wrapper
- `scripts/load_neo4j.py` — load data into Neo4j
- `tests/` — automated tests

## Run tests

```bash
make test
```

## Example question and answer

Example request:

```json
{
  "question": "Who founded NovaPay?"
}
```

Example response idea:

```json
{
  "answer": "NovaPay was founded by Elena Rossi in 2016.",
  "template_id": "embedding"
}
```

## Streamlit UI

The Streamlit frontend lives in [app/frontend/streamlit_app.py](app/frontend/streamlit_app.py).

Run it with:

```bash
streamlit run app/frontend/streamlit_app.py
```

## Notes

- The LLM never generates Cypher.
- The retrieval path is embedding-first and uses lightweight similarity
  matching over node names.
- The graph context is kept intentionally simple so the app remains easy to
  run locally.
