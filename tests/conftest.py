from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_neo4j_client():
    client = MagicMock()
    client.verify_connectivity.return_value = (True, "ok")
    client.run_read.return_value = []
    return client
