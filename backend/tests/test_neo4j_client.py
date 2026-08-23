"""run_read must reach the driver with READ routing and the timeout.

Read access mode is the query service's read-only control (Community has no
RBAC — docs/fragilities.md #15), so a test that it is actually requested is
guarding a security property, not an implementation detail.
"""

import pytest
from neo4j import RoutingControl

from graph.neo4j_client import Neo4jClient


class _Records:
    records: list = []


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_query(self, query, params=None, **kwargs):
        self.calls.append({"query": query, "params": params, **kwargs})
        return _Records()

    async def close(self) -> None:  # pragma: no cover - not exercised here
        pass


@pytest.fixture
def client(monkeypatch) -> Neo4jClient:
    monkeypatch.setattr(
        "graph.neo4j_client.AsyncGraphDatabase.driver",
        lambda *a, **k: _FakeDriver(),
    )
    # A password must be set or __init__ refuses to construct.
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "neo4j_password", "x", raising=False)
    return Neo4jClient()


@pytest.mark.asyncio
async def test_run_read_requests_read_routing_and_timeout(client):
    await client.run_read("MATCH (n) RETURN n", timeout_s=3.0, foo=1)
    call = client._driver.calls[0]  # noqa: SLF001 — asserting the driver call
    assert call["routing_"] == RoutingControl.READ
    assert call["timeout"] == 3.0
    assert call["params"] == {"foo": 1}


@pytest.mark.asyncio
async def test_run_read_omits_timeout_when_unset(client):
    await client.run_read("MATCH (n) RETURN n")
    assert "timeout" not in client._driver.calls[0]  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_defaults_to_write_routing(client):
    """Ingestion writes through run(); it must NOT carry read routing."""
    await client.run("MATCH (n) RETURN n")
    assert "routing_" not in client._driver.calls[0]  # noqa: SLF001
