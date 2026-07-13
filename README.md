# 🕸️ Tech / Startups GraphRAG

A production-shaped GraphRAG system over a Neo4j knowledge graph of startups,
founders, investors, products, and awards — with a FastAPI backend, a Groq-powered
LLM pipeline, and an interactive Streamlit frontend. Built to deploy on Railway.

```
question in natural language
        │
        ▼
Groq LLM ── generates read-only Cypher (schema + few-shot grounded)
        │
        ▼
Cypher safety validator (blocks writes/admin ops, caps LIMIT)
        │
        ▼
Neo4j (READ transaction only) ── zero rows? → fulltext fallback search
        │
        ▼
Groq LLM ── synthesizes a grounded, citation-honest answer from the results
        │
        ▼
FastAPI /answer  →  Streamlit chat UI (answer + Cypher + raw rows + graph view)
```

## Project layout

```
.github/workflows/ci.yml     Lint + data pipeline + test suite on every push
app/
  main.py                    FastAPI app entrypoint
  config.py                  Env-driven settings (pydantic-settings)
  schemas.py                 Request/response models
  db/neo4j_client.py         Pooled Neo4j driver, enforced READ transactions
  core/
    ontology.py              Graph schema description + few-shot Cypher examples
    prompts.py                Annotated system prompts (Cypher gen + answer synthesis)
    llm.py                     Groq chat completion wrapper (retried)
    graph_rag.py                Orchestration: generate → validate → run → fallback → synthesize
  api/routes.py               GET /health, POST /answer
  frontend/streamlit_app.py   Interactive chat UI, Cypher/results/graph tabs
data/
  raw/                        Original messy source-system exports (11 CSVs)
  processed/                  Cleaned CSVs + cleaning_report.md (generated, gitignored)
  schema/ontology.md          Ontology design write-up
scripts/
  clean_data.py                Real ETL: dedup, normalize, resolve dangling refs
  load_neo4j.py                 Idempotent MERGE-based loader + constraints/indexes
tests/                        pytest suite (health, answer, Cypher safety, cleaning logic)
.env.example                  All required environment variables
railway.json / Procfile       Railway deploy config (API + Streamlit as 2 services)
Makefile                      One-line commands for the full workflow
ruff.toml / pytest.ini        Lint/test config
```

## Ontology

See [`data/schema/ontology.md`](data/schema/ontology.md) for the full write-up.
Short version:

- **Nodes:** `Person`, `Company`, `Investor`, `Product`, `Award`
- **Relationships:** `(Person)-[FOUNDED]->(Company)`, `(Person)-[WORKS_AT]->(Company)`,
  `(Investor)-[INVESTED_IN]->(Company)`, `(Company)-[ACQUIRED]->(Company)`,
  `(Company)-[DEVELOPS]->(Product)`, `(Person|Company)-[WON]->(Award)`

## Data quality handling

The raw export is intentionally messy (this is what the assignment asked for).
`scripts/clean_data.py` fixes, rather than hides, these issues:

| Issue in raw data | Fix |
|---|---|
| Exact duplicate rows (e.g. repeated Elena Rossi, GreenGrid, DataForge/Marcus Chen, ForgeML) | Dropped after normalization |
| Inconsistent casing (`founder` vs `Founder`, `ai` vs `AI`, `elena rossi`/`novapay`) | Canonical-name index resolves every relationship endpoint to one display name |
| Mixed boolean formats (`yes`/`Y`/`TRUE`/`FALSE`) | Parsed to real booleans |
| Mixed numeric formats (`2016.0`) | Coerced to nullable ints/floats |
| Missing values (hometown, valuation, founded year, launch year) | Kept as `NULL`, never guessed |
| Dangling references (`Phantom Capital` investor, `GhostApp` company not in their node sheets) | Loaded as explicit placeholder nodes tagged `data_quality: "placeholder"` so the edge isn't silently dropped, and the LLM is instructed to flag them as lower-confidence |

Run `python scripts/clean_data.py` and read `data/processed/cleaning_report.md`
for the full, itemized before/after log.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
make install

cp .env.example .env
# fill in NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD and GROQ_API_KEY

make clean          # data/raw -> data/processed
make load-graph      # data/processed -> Neo4j (idempotent, safe to re-run)
make test             # pytest, mocks Neo4j/Groq so no live services needed

make api               # FastAPI on :8000  (in one terminal)
make frontend            # Streamlit on :8501 (in another terminal)
```

Open `http://localhost:8501`. The sidebar's health indicator confirms both
Neo4j and Groq are reachable before you start asking questions.

## API

### `GET /health`
Reports Neo4j connectivity and whether `GROQ_API_KEY` is configured.

```json
{
  "status": "ok",
  "components": [
    {"name": "neo4j", "status": "ok", "detail": "connected"},
    {"name": "groq", "status": "ok", "detail": "GROQ_API_KEY is set"}
  ],
  "version": "1.0.0"
}
```

### `POST /answer`
```json
{ "question": "Who founded NovaPay?", "top_k": 25, "include_subgraph": true }
```
Returns the synthesized answer, the exact Cypher that was generated and run,
its validation status, the raw rows, an optional node/edge subgraph for
visualization, any warnings (e.g. placeholder-node results, fallback search
used), latency, and which model answered.

## Cypher safety

LLM-generated Cypher is treated as untrusted input:

1. A regex-based validator (`app/core/graph_rag.py::validate_cypher`) rejects
   any query containing `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH`,
   `REMOVE`, `DROP`, `LOAD CSV`, mutating APOC procedures, or admin commands,
   and rejects queries with no `RETURN`.
2. Every query is additionally forced to run inside an explicit Neo4j **READ**
   transaction (`session.execute_read`), which Neo4j itself will refuse to
   run a write against — defense in depth, not just prompt trust.
3. `LIMIT` is always present (auto-appended if missing) and capped server-side
   to `MAX_CYPHER_ROWS` regardless of what the LLM requested.
4. If the query runs but returns zero rows, a fulltext fallback search finds
   the closest-matching entity names instead of just failing.

## Deploying to Railway

This repo runs as **two Railway services** from the same GitHub repo:

1. **API service** — uses `railway.json` as-is (`uvicorn app.main:app`).
   Set `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `GROQ_API_KEY` (and
   optionally `NEO4J_DATABASE`, `CORS_ORIGINS`) as Railway environment
   variables.
2. **Frontend service** — same repo, override the start command to:
   `streamlit run app/frontend/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
   (this is also predefined as the `frontend` process in the `Procfile`).
   Set `BACKEND_URL` to the API service's public Railway URL.
3. **Neo4j** — use [Neo4j AuraDB](https://neo4j.com/cloud/platform/aura-graph-database/)
   (free tier works fine for this dataset) or Railway's Neo4j template; point
   `NEO4J_URI` at it.
4. After both services are up, run `python scripts/load_neo4j.py` once
   (locally, pointed at the Aura instance via `.env`, or as a one-off Railway
   run) to populate the graph.

## Testing & linting

```bash
make test    # pytest — health, answer endpoint, Cypher validator, cleaning logic
make lint     # ruff check
make format    # ruff format
```

CI (`.github/workflows/ci.yml`) runs lint, the cleaning pipeline, and the
full test suite on every push/PR — no live Neo4j/Groq needed since the
backend tests mock the Neo4j client and stub the LLM calls.
