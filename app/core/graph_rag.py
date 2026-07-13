"""
graph_rag.py
=============
Orchestrates the full pipeline for a single question:

    question
      -> match a fixed, hand-written Cypher template (app/core/cypher_library.py)
      -> [no match] fall back to fulltext entity search
      -> validate the resolved Cypher is read-only (defense in depth)
      -> execute against Neo4j (parameterized, read-only transaction)
      -> [zero rows] fall back to fulltext entity search + retry once
      -> build a node/edge subgraph payload for the frontend visualization
      -> (LLM) synthesize a grounded natural-language answer from the rows

The LLM is used for exactly one thing: turning retrieved graph rows into
a natural-language answer. It never sees the question with the intent of
writing Cypher, and it never touches Neo4j -- every query that can run
against the database is one of the fixed templates in
`app/core/cypher_library.py` (documented in full in `CYPHER.md`) or the
single fulltext fallback query defined below.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.core.cypher_library import CypherMatch, match_question
from app.core.llm import chat_completion
from app.core.prompts import ANSWER_SYSTEM_PROMPT, ANSWER_USER_TEMPLATE
from app.db.neo4j_client import Neo4jClient

logger = logging.getLogger("app.graph_rag")

# Defense-in-depth check on every resolved query before it runs, even
# though every query originates from the fixed template library rather
# than free-form LLM output. Catches a mistake in a template itself
# (e.g. a future template that forgets it must be read-only) before it
# ever reaches Neo4j, on top of `Neo4jClient.run_read` enforcing an
# explicit READ transaction.
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
    template_id: str | None = None
    template_description: str | None = None
    results: list[dict] = field(default_factory=list)
    used_fallback_search: bool = False
    warnings: list[str] = field(default_factory=list)
    answer: str = ""
    model_used: str = ""
    latency_ms: int = 0


def validate_cypher(query: str) -> CypherValidation:
    """Read-only / well-formedness check, run against every resolved query."""
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
ORDER BY score DESC LIMIT 10
"""


def _fallback_search_term(question: str) -> str:
    """
    Crude but effective: use capitalized word sequences from the question
    as the fulltext search term, falling back to the raw question if none
    are found (e.g. an all-lowercase question).
    """
    candidates = re.findall(r"[A-Z][a-zA-Z0-9&'\-]*(?:\s+[A-Z][a-zA-Z0-9&'\-]*)*", question)
    return " ".join(candidates) if candidates else question


def fallback_entity_search(db: Neo4jClient, question: str) -> list[dict]:
    """
    Used when either (a) no Cypher template matched the question, or
    (b) a template matched but its query returned zero rows -- usually
    because the entity name in the question doesn't exactly match the
    stored name (typo, partial name, different casing). Runs a fulltext
    search over node names/descriptions to surface the closest matching
    entities instead of just returning "no results".
    """
    term = _fallback_search_term(question)
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


def _unanswerable_result(question: str, answer_model: str, warnings: list[str], start: float) -> RagResult:
    latency_ms = int((time.perf_counter() - start) * 1000)
    return RagResult(
        question=question,
        cypher="",
        cypher_params={},
        cypher_valid=False,
        template_id=None,
        template_description=None,
        results=[],
        warnings=warnings,
        answer=(
            "I wasn't able to match that question to one of the graph's predefined "
            "query templates, and a fulltext search didn't surface a close entity "
            "match either. Try rephrasing it to reference a specific company, "
            "person, investor, product, or award by name -- for example "
            "\"Who founded NovaPay?\" or \"Which investors backed GreenGrid?\"."
        ),
        model_used=answer_model,
        latency_ms=latency_ms,
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
    answer_model = model_override or settings.groq_answer_model
    max_rows = max(1, min(top_k, settings.max_cypher_rows))
    warnings: list[str] = []

    match: CypherMatch | None = match_question(question)

    if match is None:
        # No fixed template recognized this question at all -- go
        # straight to the fulltext fallback rather than running nothing.
        fallback_rows = fallback_entity_search(db, question)
        if not fallback_rows:
            return _unanswerable_result(question, answer_model, warnings, start)
        warnings.append(
            "No predefined Cypher query template matched this question; showing "
            "closest-matching entities from a fulltext search instead."
        )
        cypher, cypher_params = FULLTEXT_FALLBACK_QUERY.strip(), {"term": _fallback_search_term(question) + "~"}
        results = fallback_rows
        used_fallback = True
        template_id, template_description = "fulltext_fallback", "Fulltext search over node names/descriptions."
    else:
        cypher = match.cypher
        cypher_params = {**match.params, "limit": max_rows}
        template_id, template_description = match.template_id, match.description

        validation = validate_cypher(cypher)
        if not validation.valid:
            # Should be unreachable for a library query -- kept as a hard
            # safety net in case a future template is added incorrectly.
            warnings.append(f"Matched template failed the read-only safety check ({validation.reason}).")
            return _unanswerable_result(question, answer_model, warnings, start)

        try:
            results = db.run_read(cypher, cypher_params)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Cypher execution error: {exc}")
            results = []

        used_fallback = False
        if not results:
            fallback_rows = fallback_entity_search(db, question)
            if fallback_rows:
                used_fallback = True
                warnings.append(
                    "The matched query returned no rows; showing closest-matching "
                    "entities from a fulltext search instead."
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
        cypher_params=cypher_params,
        cypher_valid=True,
        template_id=template_id,
        template_description=template_description,
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
