"""Pydantic models for API requests/responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthComponent(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    components: list[HealthComponent]
    version: str


class AnswerRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="Natural-language question about the graph.")
    top_k: int = Field(default=25, ge=1, le=200, description="Max rows to pull back from Neo4j.")
    include_subgraph: bool = Field(default=True, description="Whether to return a node/edge payload for graph visualization.")
    model: str | None = Field(default=None, description="Optional Groq model override for this request.")


class GraphNode(BaseModel):
    id: str
    label: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class Subgraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    question: str
    answer: str
    cypher: str
    cypher_valid: bool
    row_count: int
    results: list[dict[str, Any]]
    subgraph: Subgraph | None = None
    used_fallback_search: bool = False
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int
    model_used: str
