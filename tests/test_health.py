"""Tests for GET /health."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def test_health_ok(fake_neo4j_client):
    with patch("app.api.routes.get_neo4j_client", return_value=fake_neo4j_client):
        app.dependency_overrides.clear()
        from app.db.neo4j_client import get_neo4j_client

        app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
        client = TestClient(app)
        resp = client.get("/health")
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    names = {c["name"] for c in body["components"]}
    assert names == {"neo4j", "groq"}


def test_health_reports_down_neo4j(fake_neo4j_client):
    fake_neo4j_client.verify_connectivity.return_value = (False, "service unavailable")
    from app.db.neo4j_client import get_neo4j_client

    app.dependency_overrides[get_neo4j_client] = lambda: fake_neo4j_client
    client = TestClient(app)
    resp = client.get("/health")
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "down"
