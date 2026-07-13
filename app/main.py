"""FastAPI application entrypoint."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()

app = FastAPI(
    title="Tech/Startups GraphRAG API",
    description=(
        "GraphRAG backend over a Neo4j knowledge graph of startups, founders, "
        "investors, products, and awards. Natural-language questions are matched "
        "against a fixed library of hand-written, read-only Cypher templates "
        "(see CYPHER.md) -- never LLM-generated -- executed against Neo4j, and "
        "the retrieved rows are synthesized into a grounded answer by a "
        "Groq-hosted LLM."
    ),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", include_in_schema=False)
def root():
    return {"service": "graphrag-startups-api", "version": __version__, "docs": "/docs"}
