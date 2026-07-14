"""Tests for POST /answer and /ask, and for the Cypher safety validator."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.graph_rag import validate_cypher
from app.db.neo4j_client import get_neo4j_client
from app.main import app

# ---------------------------------------------------------------------------
# Cypher validator unit tests (no DB needed)
# ---------------------------------------------------------------------------

def test_validate_cypher_rejects_write_queries():
    result = validate_cypher("MATCH (n) DETACH DELETE n RETURN n LIMIT 1")
    assert result.valid is False


def test_validate_cypher_rejects_missing_return():
    result = validate_cypher("MATCH (n:Company) LIMIT 5")
    assert result.valid is False


def test_validate_cypher_rejects_missing_limit():
    result = validate_cypher("MATCH (c:Company) RETURN c.name")
    assert result.valid is False


def test_validate_cypher_accepts_clean_read_query_with_literal_limit():
    result = validate_cypher("MATCH (c:Company) RETURN c.name LIMIT 10")
    assert result.valid is True


def test_validate_cypher_accepts_parameterized_limit():
    result = validate_cypher("MATCH (c:Company) RETURN c.name LIMIT $limit")
    assert result.valid is True


# ---------------------------------------------------------------------------
# Helper: a fake subgraph row that the traversal query returns
# ---------------------------------------------------------------------------

def _fake_subgraph_row(name: str = "NovaPay", label: str = "Company") -> dict:
    """Mimics the shape returned by SUBGRAPH_TRAVERSAL_QUERY."""
    return {
        "start": {"name": name},
        "start_label": label,
        "edges": [],
    }


# ---------------------------------------------------------------------------
# Integration tests for /ask and /answer endpoints
# ---------------------------------------------------------------------------

def test_ask_endpoint_alias_returns_answer(fake_neo4j_client):
    """The legacy /ask endpoint should remain compatible with the current /answer API."""
    # First call: find_top_nodes candidate scan
    # Second+ calls: retrieve_subgraph traversal per found node
    fake_neo4j_client.run_read.side_effect = [
        [{"label": "Company", "name": "NovaPay"}],  # candidate scan
        [_fake_subgraph_row()],                      # subgraph traversal
    ]
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    with patch("app.core.graph_rag.generate_answer", return_value="Elena Rossi founded NovaPay in 2016."):
        resp = client.post("/ask", json={"question": "Who founded NovaPay?"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Elena Rossi founded NovaPay in 2016."


def test_answer_endpoint_end_to_end(fake_neo4j_client):
    """An embedding-based similarity search returns candidate entities and the answer is synthesized."""
    fake_neo4j_client.run_read.side_effect = [
        [{"label": "Company", "name": "NovaPay"}],  # candidate scan
        [_fake_subgraph_row()],                      # subgraph traversal
    ]
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    with patch("app.core.graph_rag.generate_answer", return_value="Elena Rossi founded NovaPay in 2016."):
        resp = client.post("/answer", json={"question": "Who founded NovaPay?"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Elena Rossi founded NovaPay in 2016."
    assert body["cypher_valid"] is True
    assert body["template_id"] == "embedding"
    assert body["row_count"] == 1


def test_answer_endpoint_uses_embedding_similarity_when_no_direct_match(fake_neo4j_client):
    """A question with no exact entity match is answered using embedding-based similarity retrieval."""
    # The embedding scorer will still pick NovaPay as closest match for gibberish,
    # so the pipeline proceeds normally — just with low confidence scores.
    fake_neo4j_client.run_read.side_effect = [
        [{"label": "Company", "name": "NovaPay"}],  # candidate scan
        [_fake_subgraph_row()],                      # subgraph traversal
    ]
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    with patch("app.core.graph_rag.generate_answer", return_value="Closest match: NovaPay."):
        resp = client.post("/answer", json={"question": "asdkjaslkdjaslkd random gibberish"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    # Embedding search found a result, so template_id is set and cypher_valid is True
    assert body["template_id"] == "embedding"
    assert body["cypher_valid"] is True


def test_answer_endpoint_never_runs_when_neo4j_and_search_both_find_nothing(fake_neo4j_client):
    """When the DB returns nothing, the pipeline returns a clean unanswerable result."""
    fake_neo4j_client.run_read.return_value = []
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    resp = client.post("/answer", json={"question": "asdkjaslkdjaslkd random gibberish"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["cypher_valid"] is False
    assert body["row_count"] == 0
    assert body["template_id"] is None
