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
from app.core.llm import ContextTooLargeError, chat_completion
from app.core.prompts import ANSWER_SYSTEM_PROMPT, ANSWER_USER_TEMPLATE
from app.db.neo4j_client import Neo4jClient

logger = logging.getLogger("app.graph_rag")

# ── Schema ─────────────────────────────────────────────────────────────────
# Single source of truth for node labels present in this graph.
NODE_LABELS: list[str] = ["Company", "Person", "Investor", "Product", "Award"]

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


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: Subgraph Traversal Query
#
# Uses a double-UNWIND pattern so that:
#   • Multi-hop paths expose ALL intermediate edges, not just the last hop
#   • startNode(r)/endNode(r) always reflect the true stored direction of each rel
#   • properties(r) brings relationship metadata (role, round, amountMillion…)
#     to the LLM context for richer, more accurate answers
# ─────────────────────────────────────────────────────────────────────────────
SUBGRAPH_TRAVERSAL_QUERY = """
MATCH (start)
WHERE labels(start)[0] = $label AND start.name = $name

OPTIONAL MATCH path = (start)-[*1..{hops}]-(neighbor)

WITH start, collect(DISTINCT path) AS paths

UNWIND CASE WHEN size(paths) > 0 THEN paths ELSE [null] END AS p

UNWIND CASE WHEN p IS NOT NULL THEN relationships(p) ELSE [null] END AS r

WITH start,
     collect(DISTINCT CASE WHEN r IS NOT NULL THEN {{
         from:     startNode(r).name,
         rel:      type(r),
         to:       endNode(r).name,
         to_label: labels(endNode(r))[0],
         props:    properties(r)
     }} END) AS all_edges

RETURN start,
       labels(start)[0] AS start_label,
       [e IN all_edges WHERE e IS NOT NULL] AS edges
"""


# ANSWER_SYSTEM_PROMPT and ANSWER_USER_TEMPLATE are imported from app.core.prompts


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


def _format_rel_props(props: dict[str, Any]) -> str:
    """Render relationship property dict as a compact human-readable string.

    Booleans are shown as Yes/No. Internal / embedding fields are skipped.
    Example output:  role=CEO, current=Yes
    """
    skip = {"embedding", "embedding_text"}
    parts = []
    for k, v in props.items():
        if k in skip or v is None:
            continue
        if isinstance(v, bool):
            parts.append(f"{k}={'Yes' if v else 'No'}")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


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


def find_top_nodes(db: Neo4jClient, question: str, top_k: int = 5) -> list[dict]:
    """Stage 1: Vector Search — fetch candidate nodes then rank by embedding similarity.

    Fetches all named nodes from the known label set, ranks them against
    the question using a combined embedding + keyword-overlap score, and
    returns the top-k hits.
    """
    labels_literal = ", ".join(f"'{lbl}'" for lbl in NODE_LABELS)
    try:
        rows = db.run_read(
            f"MATCH (n) WHERE labels(n)[0] IN [{labels_literal}] "
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
    """Stage 2: Graph Traversal — collect all edges within `hops` of the start node.

    Returns a dict with keys:
        center  — Neo4j node object for the starting entity
        label   — label string of the starting entity
        edges   — list of {from, rel, to, to_label, props} dicts
    """
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
        "edges": [
            e for e in row.get("edges", [])
            if isinstance(e, dict) and e.get("from") and e.get("to")
        ],
    }


def subgraph_to_context(subgraph: dict) -> str:
    """Stage 3: Context Assembly — serialise a subgraph into LLM-readable text.

    Format:
        ENTITY: <name> [<label>]
        Properties: key=value, ...
        CONNECTIONS:
          • Source –[RELATIONSHIP]→ Target  (prop=val, ...)
    """
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

        # Surface relationship properties to the LLM
        rel_props = {
            k: v for k, v in (edge.get("props") or {}).items()
            if k not in skip and v is not None
        }
        prop_str = _format_rel_props(rel_props)
        suffix = f"  ({prop_str})" if prop_str else ""

        lines.append(f"  • {edge['from']}  –[{edge['rel']}]→  {edge['to']}{suffix}")

    return "\n".join(lines)


# ~4 chars per token on average; reserve 700 output + ~600 for system+user overhead
# → 12 000 TPM limit − 1 300 buffer = 10 700 usable input tokens ≈ 42 800 chars
_CONTEXT_CHAR_BUDGET = 20_000  # conservative for the free tier (12 k TPM)


def _truncate_context(context: str, budget: int = _CONTEXT_CHAR_BUDGET) -> tuple[str, bool]:
    """Trim context to `budget` characters, cutting at a clean block boundary.

    Returns ``(truncated_context, was_truncated)``.
    """
    if len(context) <= budget:
        return context, False

    # Try to cut at an entity-block separator to avoid mid-block cuts
    separator = "\n\n───────────────────────────────────\n\n"
    truncated = context[:budget]
    last_sep = truncated.rfind(separator)
    if last_sep > 0:
        truncated = truncated[:last_sep]
    return truncated, True


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
    """Answer a question about the startup ecosystem using the full GraphRAG pipeline.

    Returns a RagResult with the answer, retrieved nodes, and (optionally) a
    subgraph payload for visualization.
    """
    settings = get_settings()
    start = time.perf_counter()
    answer_model = model_override or settings.groq_answer_model
    max_rows = max(1, min(top_k, settings.max_cypher_rows))
    warnings: list[str] = []

    # ── Stage 1: Vector Search ────────────────────────────────────────────────
    results = find_top_nodes(db, question, top_k=max_rows)
    used_fallback = False

    if not results:
        # Try fulltext index; embedding search IS find_top_nodes, so skip re-running it
        fallback_rows = fallback_entity_search(db, question, top_k=max_rows)
        if not fallback_rows:
            return _unanswerable_result(question, answer_model, warnings, start)
        warnings.append("Showing closest-matching entities from a fulltext similarity search.")
        results = fallback_rows
        used_fallback = True

    # ── Stages 2 & 3: Graph Traversal + Context Assembly ─────────────────────
    subgraph_nodes: list[dict] = []
    subgraph_edges: list[dict] = []
    context_blocks: list[str] = []
    # name → node_id: used to resolve correct source/target for visualization edges
    name_to_id: dict[str, str] = {}

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
            center_name = center_props.get("name", "unknown")
            center_id = f"{center_label}:{center_name}"

            if center_name not in name_to_id:
                name_to_id[center_name] = center_id
                subgraph_nodes.append({
                    "id": center_id,
                    "label": center_label,
                    "name": center_name,
                    "properties": center_props,
                })

            for edge in sg.get("edges", []):
                source_name = edge.get("from")
                target_name = edge.get("to")
                target_label = edge.get("to_label") or "Unknown"
                if not source_name or not target_name:
                    continue

                # Register target node if unseen
                target_id = f"{target_label}:{target_name}"
                if target_name not in name_to_id:
                    name_to_id[target_name] = target_id
                    subgraph_nodes.append({
                        "id": target_id,
                        "label": target_label,
                        "name": target_name,
                        "properties": {},
                    })

                # Register source node if it's a neighbor not yet seen (2-hop edges)
                if source_name not in name_to_id:
                    source_id = f"Unknown:{source_name}"
                    name_to_id[source_name] = source_id
                    subgraph_nodes.append({
                        "id": source_id,
                        "label": "Unknown",
                        "name": source_name,
                        "properties": {},
                    })

                # Use actual edge direction from the graph, not always from center
                edge_props = dict(edge.get("props") or {})
                edge_props["to_label"] = target_label
                subgraph_edges.append({
                    "source": name_to_id[source_name],
                    "target": name_to_id[target_name],
                    "type": edge.get("rel") or "RELATED",
                    "properties": edge_props,
                })

    # ── Guard: don't call LLM with empty context ──────────────────────────────
    if not context_blocks:
        return _unanswerable_result(question, answer_model, warnings, start)

    # ── Stage 4: LLM Reasoning ────────────────────────────────────────────────
    context = "\n\n───────────────────────────────────\n\n".join(context_blocks)
    context, was_truncated = _truncate_context(context)
    if was_truncated:
        warnings.append(
            "The retrieved context was trimmed to fit the model's token limit. "
            "Try reducing 'Max rows returned' in the sidebar for more complete results."
        )
    try:
        answer_text = generate_answer(question, context, model=answer_model)
    except ContextTooLargeError:
        answer_text = (
            "The graph context for this question is too large for the current model tier. "
            "Please reduce 'Max rows returned' in the sidebar (try 5–10) and ask again."
        )
        warnings.append("Groq 413: context exceeded the model's token-per-minute limit.")
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
