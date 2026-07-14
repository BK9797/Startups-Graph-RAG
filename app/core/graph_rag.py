"""
graph_rag.py
=============
Vector-first GraphRAG pipeline for the startups knowledge graph.

Pipeline stages:
  1. Vector Search    — find the most relevant graph nodes for the question
  2. Graph Traversal  — walk the neighbourhood of each relevant node
  3. Context Assembly — serialise the subgraph into structured text
  4. LLM Reasoning    — generate a natural language answer from that context
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.core.embedding import find_best_matches
from app.core.llm import chat_completion
from app.core.prompts import ANSWER_SYSTEM_PROMPT, ANSWER_USER_TEMPLATE
from app.db.neo4j_client import Neo4jClient

logger = logging.getLogger("app.graph_rag")

FORBIDDEN_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL\s*\{|LOAD\s+CSV|"
    r"apoc\.periodic|apoc\.create|apoc\.merge|GRANT|REVOKE|DENY)\b",
    re.IGNORECASE,
)


@dataclass
class CypherValidation:
    valid: bool
    reason: str | None = None


@dataclass
class RagResult:
    question: str
    cypher: str
    cypher_params: dict = field(default_factory=dict)
    cypher_valid: bool = True
    retrieval_mode: str | None = None
    results: list[dict] = field(default_factory=list)
    used_fallback_search: bool = False
    warnings: list[str] = field(default_factory=list)
    answer: str = ""
    model_used: str = ""
    latency_ms: int = 0


def validate_cypher(query: str) -> CypherValidation:
    """Read-only / well-formedness check, kept for compatibility."""
    stripped = query.strip().rstrip(";")
    if not stripped:
        return CypherValidation(False, "empty query")
    if FORBIDDEN_KEYWORDS.search(stripped):
        return CypherValidation(False, "query contains a forbidden write/admin keyword")
    if not re.search(r"\bRETURN\b", stripped, re.IGNORECASE):
        return CypherValidation(False, "query has no RETURN clause")
    if not re.search(r"\bLIMIT\s+(\d+|\$\w+)\b", stripped, re.IGNORECASE):
        return CypherValidation(False, "query has no LIMIT clause")
    return CypherValidation(True, None)


FULLTEXT_FALLBACK_QUERY = """
CALL db.index.fulltext.queryNodes('entity_search', $term) YIELD node, score
RETURN labels(node)[0] AS label, node.name AS name, score
ORDER BY score DESC LIMIT $limit
"""


SUBGRAPH_TRAVERSAL_QUERY = """
MATCH (start)
WHERE labels(start)[0] = $label AND start.name = $name

OPTIONAL MATCH path = (start)-[*1..{hops}]-(neighbor)

WITH start,
     collect(DISTINCT {{
         from: startNode(last(relationships(path))).name,
         rel:  type(last(relationships(path))),
         to:   endNode(last(relationships(path))).name,
         to_label: labels(endNode(last(relationships(path))))[0]
     }}) AS edges

RETURN start, labels(start)[0] AS start_label, edges
"""


# SYSTEM_PROMPT and ANSWER_USER_TEMPLATE are imported from app.core.prompts


def _serialize_properties(node: Any) -> dict[str, Any]:
    """Convert a Neo4j node object or plain dict to a plain Python dict,
    stripping internal embedding fields."""
    skip = {"embedding", "embedding_text"}
    if hasattr(node, "items"):
        items = node.items()
    else:
        try:
            items = dict(node).items()
        except Exception:  # noqa: BLE001
            return {}
    return {k: v for k, v in items if k not in skip and v is not None}


def _fallback_search_term(question: str) -> str:
    candidates = re.findall(r"[A-Z][a-zA-Z0-9&'\-]*(?:\s+[A-Z][a-zA-Z0-9&'\-]*)*", question)
    return " ".join(candidates) if candidates else question


def fallback_entity_search(db: Neo4jClient, question: str, top_k: int = 5) -> list[dict]:
    """Fulltext-index fallback when embedding search returns no results."""
    term = _fallback_search_term(question)
    try:
        return db.run_read(FULLTEXT_FALLBACK_QUERY, {"term": term + "~", "limit": top_k})
    except Exception as exc:  # noqa: BLE001
        logger.info("Fulltext fallback failed (index may not exist yet): %s", exc)
        return []


def embedding_fallback_search(db: Neo4jClient, question: str, top_k: int = 5) -> list[dict]:
    """Pure embedding fallback — no fulltext index required."""
    try:
        rows = db.run_read(
            "MATCH (n) WHERE labels(n)[0] IN ['Company', 'Person', 'Investor', 'Product', 'Award'] "
            "AND n.name IS NOT NULL "
            "RETURN labels(n)[0] AS label, n.name AS name LIMIT 500"
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Embedding fallback failed: %s", exc)
        return []

    candidates = [
        {"label": row.get("label", "Entity"), "name": row["name"].strip()}
        for row in rows
        if isinstance(row.get("name"), str) and row["name"].strip()
    ]

    if not candidates:
        return []

    ranked = find_best_matches(question, [item["name"] for item in candidates], top_k=top_k)
    found = []
    for score, name in ranked:
        label = next((item["label"] for item in candidates if item["name"] == name), "Entity")
        found.append({"label": label, "name": name, "score": round(score, 4), "source": "embedding"})
    return found


def find_top_nodes(db: Neo4jClient, question: str, top_k: int = 5) -> list[dict]:
    """Stage 1: Vector Search — fetch candidate nodes then rank by embedding similarity."""
    try:
        rows = db.run_read(
            "MATCH (n) WHERE labels(n)[0] IN ['Company', 'Person', 'Investor', 'Product', 'Award'] "
            "AND n.name IS NOT NULL "
            "RETURN labels(n)[0] AS label, n.name AS name LIMIT 500"
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Vector search failed: %s", exc)
        return []

    candidates = [
        {"label": row.get("label", "Entity"), "name": row["name"].strip()}
        for row in rows
        if isinstance(row.get("name"), str) and row["name"].strip()
    ]

    if not candidates:
        return []

    ranked = find_best_matches(question, [item["name"] for item in candidates], top_k=top_k)
    found: list[dict] = []
    for score, name in ranked:
        label = next((item["label"] for item in candidates if item["name"] == name), "Entity")
        found.append({"label": label, "name": name, "score": round(score, 4), "source": "embedding"})
    return found


def retrieve_subgraph(node_name: str, node_label: str, db: Neo4jClient, hops: int = 2) -> dict:
    query = SUBGRAPH_TRAVERSAL_QUERY.format(hops=hops)
    try:
        rows = db.run_read(query, {"name": node_name, "label": node_label})
    except Exception as exc:  # noqa: BLE001
        logger.info("Subgraph traversal failed: %s", exc)
        return {}

    if not rows:
        return {}

    row = rows[0]
    return {
        "center": row.get("start") or {},
        "label": row.get("start_label", node_label),
        "edges": [e for e in row.get("edges", []) if isinstance(e, dict) and e.get("from") and e.get("to")],
    }


def subgraph_to_context(subgraph: dict) -> str:
    if not subgraph or not subgraph.get("center"):
        return ""

    center = subgraph["center"]
    label = subgraph.get("label", "")
    skip = {"embedding", "embedding_text"}
    props = ", ".join(
        f"{k}={v}"
        for k, v in center.items()
        if k not in skip and v is not None
    )

    lines = [
        f"ENTITY: {center.get('name', 'Unknown')} [{label}]",
        f"Properties: {props}",
        "",
        "CONNECTIONS:",
    ]

    seen: set[tuple[str, str, str]] = set()
    for edge in subgraph.get("edges", []):
        triple = (edge["from"], edge["rel"], edge["to"])
        if triple in seen:
            continue
        seen.add(triple)
        lines.append(f"  • {edge['from']}  –[{edge['rel']}]→  {edge['to']}")

    return "\n".join(lines)


def generate_answer(question: str, context: str, model: str) -> str:
    """Stage 4: LLM Reasoning — synthesise a grounded answer from the assembled context."""
    user_prompt = ANSWER_USER_TEMPLATE.format(question=question, context=context)
    return chat_completion(
        system_prompt=ANSWER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        temperature=get_settings().groq_temperature_answer,
        max_tokens=700,
    )


def _unanswerable_result(question: str, answer_model: str, warnings: list[str], start: float) -> RagResult:
    latency_ms = int((time.perf_counter() - start) * 1000)
    return RagResult(
        question=question,
        cypher="",
        cypher_params={},
        cypher_valid=False,
        retrieval_mode=None,
        results=[],
        warnings=warnings,
        answer=(
            "I couldn't find any relevant entities in the knowledge graph for that question. "
            "Try asking about a specific company, person, investor, product, or award."
        ),
        model_used=answer_model,
        latency_ms=latency_ms,
    )


def answer_question(
    db: Neo4jClient,
    question: str,
    top_k: int = 5,
    include_subgraph: bool = True,
    model_override: str | None = None,
) -> RagResult:
    settings = get_settings()
    start = time.perf_counter()
    answer_model = model_override or settings.groq_answer_model
    max_rows = max(1, min(top_k, settings.max_cypher_rows))
    warnings: list[str] = []

    results = find_top_nodes(db, question, top_k=max_rows)
    used_fallback = False

    if not results:
        fallback_rows = fallback_entity_search(db, question, top_k=max_rows)
        if not fallback_rows:
            fallback_rows = embedding_fallback_search(db, question, top_k=max_rows)
        if not fallback_rows:
            return _unanswerable_result(question, answer_model, warnings, start)

        warnings.append(
            "Showing closest-matching entities from an embedding-based similarity search."
        )
        results = fallback_rows
        used_fallback = True

    subgraph_nodes: list[dict] = []
    subgraph_edges: list[dict] = []
    context_blocks: list[str] = []
    node_ids: set[str] = set()

    if include_subgraph and results:
        for node in results:
            sg = retrieve_subgraph(node["name"], node.get("label", "Entity"), db, hops=2)
            if not sg or not sg.get("center"):
                continue

            block = subgraph_to_context(sg)
            if block:
                context_blocks.append(block)

            center = sg["center"]
            center_props = _serialize_properties(center)
            center_label = sg.get("label", "Entity")
            center_name = center_props.get("name") or center_props.get("title", "unknown")
            center_id = f"{center_label}:{center_name}"
            if center_id not in node_ids:
                node_ids.add(center_id)
                subgraph_nodes.append({
                    "id": center_id,
                    "label": center_label,
                    "name": center_name,
                    "properties": center_props,
                })

            for edge in sg.get("edges", []):
                target_label = edge.get("to_label") or "Unknown"
                target_name = edge.get("to")
                if not target_name:
                    continue
                target_id = f"{target_label}:{target_name}"
                if target_id not in node_ids:
                    node_ids.add(target_id)
                    subgraph_nodes.append({
                        "id": target_id,
                        "label": target_label,
                        "name": target_name,
                        "properties": {},
                    })
                subgraph_edges.append({
                    "source": center_id,
                    "target": target_id,
                    "type": edge.get("rel") or "RELATED",
                    "properties": {"to_label": target_label},
                })

    context = "\n\n───────────────────────────────────\n\n".join(context_blocks)
    answer_text = generate_answer(question, context, model=answer_model)
    latency_ms = int((time.perf_counter() - start) * 1000)

    result = RagResult(
        question=question,
        cypher="",
        cypher_params={},
        cypher_valid=True,
        retrieval_mode="embedding",
        results=results,
        used_fallback_search=used_fallback,
        warnings=warnings,
        answer=answer_text,
        model_used=answer_model,
        latency_ms=latency_ms,
    )
    result.__dict__["subgraph_nodes"] = subgraph_nodes
    result.__dict__["subgraph_edges"] = subgraph_edges
    return result
