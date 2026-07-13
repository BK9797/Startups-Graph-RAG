"""
Thin, connection-pooled wrapper around the Neo4j Python driver.

- Singleton driver per process (Neo4j drivers are already pooled/thread-safe,
  so we don't want to recreate one per request).
- `run_read` enforces that a query only executes inside a READ transaction,
  which is a second line of defense (on top of the Cypher-safety validator
  in graph_rag.py) against an LLM-generated query attempting a write.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j import GraphDatabase, basic_auth
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.config import get_settings

logger = logging.getLogger("app.neo4j")


class Neo4jClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=basic_auth(settings.neo4j_username, settings.neo4j_password),
        )
        self._database = settings.neo4j_database

    def close(self) -> None:
        self._driver.close()

    def verify_connectivity(self) -> tuple[bool, str]:
        try:
            self._driver.verify_connectivity()
            return True, "connected"
        except ServiceUnavailable as exc:
            return False, f"service unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def run_read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a query inside an explicit READ transaction only."""
        params = params or {}

        def _work(tx):
            result = tx.run(query, **params)
            return [record.data() for record in result]

        with self._driver.session(database=self._database, default_access_mode="READ") as session:
            try:
                return session.execute_read(_work)
            except Neo4jError as exc:
                logger.warning("Cypher execution failed: %s", exc)
                raise

    def schema_snapshot(self) -> dict[str, Any]:
        """Live counts, useful for the frontend's schema sidebar and for /health."""
        try:
            node_counts = self.run_read(
                "MATCH (n) WITH labels(n)[0] AS label, count(*) AS n RETURN label, n ORDER BY label"
            )
            rel_counts = self.run_read(
                "MATCH ()-[r]->() WITH type(r) AS rel, count(*) AS n RETURN rel, n ORDER BY rel"
            )
            return {
                "node_counts": {row["label"]: row["n"] for row in node_counts},
                "relationship_counts": {row["rel"]: row["n"] for row in rel_counts},
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


_client: Neo4jClient | None = None


def get_neo4j_client() -> Neo4jClient:
    global _client
    if _client is None:
        _client = Neo4jClient()
    return _client
