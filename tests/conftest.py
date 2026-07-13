"""Shared pytest fixtures: fake Neo4j client and env vars so the app
imports cleanly in CI without a real Neo4j/Groq connection."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.fixture
def fake_neo4j_client():
    client = MagicMock()
    client.verify_connectivity.return_value = (True, "connected")
    client.run_read.return_value = [
        {"founder": "Elena Rossi", "year": 2016},
    ]
    return client
