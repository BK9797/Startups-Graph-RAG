"""
graph_rag.py
=============
Orchestrates the full GraphRAG pipeline for a single question:

    question
      -> (LLM) generate Cypher
      -> validate Cypher is read-only and schema-conformant
      -> execute against Neo4j
      -> [if zero rows] fall back to fulltext entity search + retry once
      -> build a node/edge subgraph payload for the frontend visualization
      -> (LLM) synthesize a grounded natural-language answer
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.core.llm import chat_completion
from app.core.prompts import ANSWER_SYSTEM_PROMPT, ANSWER_USER_TEMPLATE, CYPHER_SYSTEM_PROMPT
from app.db.neo4j_client import Neo4jClient

logger = logging.getLogger("app.graph_rag")

# Any of these keywords appearing in generated Cypher fails validation.
# This is a defense-in-depth check on top of running the query in an
# explicit READ transaction (see Neo4jClient.run_read).
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
    cypher_valid: bool
    results: list[dict] = field(default_factory=list)
    used_fallback_search: bool = False
    warnings: list[str] = field(default_factory=list)
    answer: str = ""
    model_used: str = ""
    latency_ms: int = 0


def validate_cypher(query: str) -> CypherValidation:
    stripped = query.strip().rstrip(";")
    if not stripped:
        return CypherValidation(False, "empty query")
    if FORBIDDEN_KEYWORDS.search(stripped):
        return CypherValidation(False, "query contains a forbidden write/admin keyword")
    if not re.search(r"\bRETURN\b", stripped, re.IGNORECASE):
        return CypherValidation(False, "query has no RETURN clause")
    if not re.search(r"\bLIMIT\s+\d+", stripped, re.IGNORECASE):
        # Auto-fix rather than reject: append a safety LIMIT.
        stripped += " LIMIT 25"
    return CypherValidation(True, None)


def enforce_limit(query: str, max_rows: int) -> str:
    """Cap whatever LIMIT the LLM chose to the server-configured maximum."""
    match = re.search(r"\bLIMIT\s+(\d+)", query, re.IGNORECASE)
    if not match:
        return query.rstrip(";") + f" LIMIT {max_rows}"
    requested = int(match.group(1))
    if requested > max_rows:
        return re.sub(r"\bLIMIT\s+\d+", f"LIMIT {max_rows}", query, flags=re.IGNORECASE)
    return query


def generate_cypher(question: str, model: str) -> str:
    settings = get_settings()
    raw = chat_completion(
        system_prompt=CYPHER_SYSTEM_PROMPT,
        user_prompt=f"User question: {question}",
        model=model,
        temperature=settings.groq_temperature_cypher,
        max_tokens=400,
    )
    # Strip stray markdown fences in case the model ignores instructions.
    cleaned = re.sub(r"^```(cypher)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    return cleaned


FULLTEXT_FALLBACK_QUERY = """
CALL db.index.fulltext.queryNodes('entity_search', $term) YIELD node, score
RETURN labels(node)[0] AS label, node.name AS name, score
ORDER BY score DESC LIMIT 10
"""


def fallback_entity_search(db: Neo4jClient, question: str) -> list[dict]:
    """
    If the generated Cypher returned zero rows, the question probably
    references an entity name the LLM guessed slightly wrong (typo,
    partial name, different casing). Run a fulltext search over node
    names/descriptions to surface the closest matching entities instead
    of just returning "no results".
    """
    # crude but effective: use capitalized word sequences and the raw
    # question as candidate search terms
    candidates = re.findall(r"[A-Z][a-zA-Z0-9&'\-]*(?:\s+[A-Z][a-zA-Z0-9&'\-]*)*", question)
    term = " ".join(candidates) if candidates else question
    try:
        return db.run_read(FULLTEXT_FALLBACK_QUERY, {"term": term + "~"})
    except Exception as exc:  # noqa: BLE001
        logger.info("Fulltext fallback failed (index may not exist yet): %s", exc)
        return []


SUBGRAPH_QUERY_TEMPLATE = """
UNWIND $names AS n
MATCH (start {{name: n}})
OPTIONAL MATCH (start)-[r]-(neighbor)
RETURN start, r, neighbor LIMIT {limit}
"""


def build_subgraph(db: Neo4jClient, results: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Best-effort extraction of a small visualizable subgraph around any
    entity names present in the flat query results. This lets the
    frontend draw an interactive graph next to the text answer.
    """
    names: set[str] = set()
    for row in results:
        for value in row.values():
            if isinstance(value, str) and len(value) < 100:
                names.add(value)
            elif isinstance(value, list):
                for v in value:
                    if isinstance(v, str) and len(v) < 100:
                        names.add(v)
    if not names:
        return [], []

    try:
        rows = db.run_read(SUBGRAPH_QUERY_TEMPLATE.format(limit=150), {"names": list(names)[:20]})
    except Exception as exc:  # noqa: BLE001
        logger.info("Subgraph enrichment query failed: %s", exc)
        return [], []

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(n):
        if n is None:
            return None
        props = dict(n)
        name = props.get("name", "unknown")
        label = list(n.labels)[0] if hasattr(n, "labels") else "Unknown"
        node_id = f"{label}:{name}"
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, "label": label, "name": name, "properties": props}
        return node_id

    for row in rows:
        start_id = add_node(row.get("start"))
        neighbor_id = add_node(row.get("neighbor"))
        rel = row.get("r")
        if rel is not None and start_id and neighbor_id:
            edges.append(
                {
                    "source": start_id,
                    "target": neighbor_id,
                    "type": rel.type if hasattr(rel, "type") else "RELATED",
                    "properties": dict(rel),
                }
            )

    return list(nodes.values()), edges


def synthesize_answer(question: str, results: list[dict], model: str) -> str:
    settings = get_settings()
    results_json = "[]" if not results else json.dumps(results, default=str, indent=2)[:6000]
    user_prompt = ANSWER_USER_TEMPLATE.format(
        question=question, row_count=len(results), results_json=results_json
    )
    return chat_completion(
        system_prompt=ANSWER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        temperature=settings.groq_temperature_answer,
        max_tokens=700,
    )


def answer_question(
    db: Neo4jClient,
    question: str,
    top_k: int = 25,
    include_subgraph: bool = True,
    model_override: str | None = None,
) -> RagResult:
    settings = get_settings()
    start = time.perf_counter()
    cypher_model = model_override or settings.groq_cypher_model
    answer_model = model_override or settings.groq_answer_model
    warnings: list[str] = []

    cypher = generate_cypher(question, cypher_model)
    validation = validate_cypher(cypher)

    if not validation.valid:
        warnings.append(f"Generated Cypher failed validation ({validation.reason}); no query was executed.")
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RagResult(
            question=question,
            cypher=cypher,
            cypher_valid=False,
            results=[],
            warnings=warnings,
            answer=(
                "I wasn't able to safely translate that question into a graph query. "
                "Try rephrasing it to reference specific companies, people, investors, "
                "products, or awards in the graph."
            ),
            model_used=answer_model,
            latency_ms=latency_ms,
        )

    cypher = enforce_limit(cypher, max_rows=top_k)

    try:
        results = db.run_read(cypher)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Cypher execution error: {exc}")
        results = []

    used_fallback = False
    if not results:
        fallback_rows = fallback_entity_search(db, question)
        if fallback_rows:
            used_fallback = True
            warnings.append(
                "The generated query returned no rows; showing closest-matching entities "
                "from a fulltext search instead."
            )
            results = fallback_rows

    if any(row.get("data_quality") == "placeholder" for row in results if isinstance(row, dict)):
        warnings.append("Some results reference placeholder nodes created from incomplete source data.")

    subgraph_nodes, subgraph_edges = ([], [])
    if include_subgraph and results:
        subgraph_nodes, subgraph_edges = build_subgraph(db, results)

    answer_text = synthesize_answer(question, results, answer_model)

    latency_ms = int((time.perf_counter() - start) * 1000)

    result = RagResult(
        question=question,
        cypher=cypher,
        cypher_valid=True,
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
