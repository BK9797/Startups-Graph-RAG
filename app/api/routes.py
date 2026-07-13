"""API routes: GET /health and POST /answer."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app import __version__
from app.config import Settings, get_settings
from app.core.graph_rag import answer_question
from app.db.neo4j_client import Neo4jClient, get_neo4j_client
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    GraphEdge,
    GraphNode,
    HealthComponent,
    HealthResponse,
    Subgraph,
)

logger = logging.getLogger("app.api")
router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency health check")
def health(
    settings: Settings = Depends(get_settings),
    db: Neo4jClient = Depends(get_neo4j_client),
) -> HealthResponse:
    components: list[HealthComponent] = []

    neo4j_ok, neo4j_detail = db.verify_connectivity()
    components.append(
        HealthComponent(
            name="neo4j",
            status="ok" if neo4j_ok else "down",
            detail=neo4j_detail,
        )
    )

    groq_configured = bool(settings.groq_api_key)
    components.append(
        HealthComponent(
            name="groq",
            status="ok" if groq_configured else "degraded",
            detail="GROQ_API_KEY is set" if groq_configured else "GROQ_API_KEY is not set",
        )
    )

    statuses = [c.status for c in components]
    if "down" in statuses:
        overall = "down"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(status=overall, components=components, version=__version__)


@router.post("/answer", response_model=AnswerResponse, summary="Ask a natural-language question about the graph")
def answer(
    payload: AnswerRequest,
    settings: Settings = Depends(get_settings),
    db: Neo4jClient = Depends(get_neo4j_client),
) -> AnswerResponse:
    if not settings.groq_api_key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY is not configured on the server.")

    ok, detail = db.verify_connectivity()
    if not ok:
        raise HTTPException(status_code=503, detail=f"Neo4j is unreachable: {detail}")

    result = answer_question(
        db=db,
        question=payload.question,
        top_k=payload.top_k,
        include_subgraph=payload.include_subgraph,
        model_override=payload.model,
    )

    subgraph = None
    if payload.include_subgraph:
        nodes = result.__dict__.get("subgraph_nodes", [])
        edges = result.__dict__.get("subgraph_edges", [])
        subgraph = Subgraph(
            nodes=[GraphNode(**n) for n in nodes],
            edges=[GraphEdge(**e) for e in edges],
        )

    return AnswerResponse(
        question=result.question,
        answer=result.answer,
        cypher=result.cypher,
        cypher_params=result.cypher_params,
        cypher_valid=result.cypher_valid,
        template_id=result.template_id,
        template_description=result.template_description,
        row_count=len(result.results),
        results=result.results,
        subgraph=subgraph,
        used_fallback_search=result.used_fallback_search,
        warnings=result.warnings,
        latency_ms=result.latency_ms,
        model_used=result.model_used,
    )
