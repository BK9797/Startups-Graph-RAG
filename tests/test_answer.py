"""Tests for POST /answer, and for the Cypher safety validator."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.graph_rag import enforce_limit, validate_cypher
from app.db.neo4j_client import get_neo4j_client
from app.main import app


def test_validate_cypher_rejects_write_queries():
    result = validate_cypher("MATCH (n) DETACH DELETE n RETURN n LIMIT 1")
    assert result.valid is False


def test_validate_cypher_rejects_missing_return():
    result = validate_cypher("MATCH (n:Company) LIMIT 5")
    assert result.valid is False


def test_validate_cypher_accepts_clean_read_query():
    result = validate_cypher("MATCH (c:Company) RETURN c.name LIMIT 10")
    assert result.valid is True


def test_enforce_limit_caps_oversized_limit():
    q = enforce_limit("MATCH (n) RETURN n LIMIT 5000", max_rows=25)
    assert "LIMIT 25" in q


def test_enforce_limit_adds_missing_limit():
    q = enforce_limit("MATCH (n) RETURN n", max_rows=25)
    assert "LIMIT 25" in q


def test_answer_endpoint_end_to_end(fake_neo4j_client):
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    with patch("app.core.graph_rag.generate_cypher", return_value="MATCH (p:Person)-[r:FOUNDED]->(c:Company) RETURN p.name AS founder, r.year AS year LIMIT 25"), \
         patch("app.core.graph_rag.synthesize_answer", return_value="Elena Rossi founded NovaPay in 2016."):
        resp = client.post("/answer", json={"question": "Who founded NovaPay?"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Elena Rossi founded NovaPay in 2016."
    assert body["cypher_valid"] is True
    assert body["row_count"] == 1


def test_answer_endpoint_rejects_unsafe_cypher(fake_neo4j_client):
    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)

    with patch("app.core.graph_rag.generate_cypher", return_value="MATCH (n) DETACH DELETE n RETURN n"):
        resp = client.post("/answer", json={"question": "delete everything"})

    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["cypher_valid"] is False
    assert body["row_count"] == 0
