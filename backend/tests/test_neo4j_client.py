"""run_read must reach the driver with READ routing and the timeout.

Read access mode is the query service's read-only control (Community has no
RBAC — docs/fragilities.md #15), so a test that it is actually requested is
guarding a security property, not an implementation detail.
"""

import pytest
from neo4j import Query, RoutingControl
from neo4j.exceptions import ServiceUnavailable

from graph.neo4j_client import Neo4jClient


class _Records:
    records: list = []


class _FakeDriver:
    def __init__(self, connect_error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.closed = False
        self._connect_error = connect_error

    async def execute_query(self, query, params=None, **kwargs):
        self.calls.append({"query": query, "params": params, **kwargs})
        return _Records()

    async def verify_connectivity(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    async def close(self) -> None:
        self.closed = True


def _install_driver(monkeypatch, driver: _FakeDriver) -> None:
    monkeypatch.setattr(
        "graph.neo4j_client.AsyncGraphDatabase.driver",
        lambda *a, **k: driver,
    )
    # A password must be set or __init__ refuses to construct.
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "neo4j_password", "x", raising=False)


@pytest.fixture
def client(monkeypatch) -> Neo4jClient:
    _install_driver(monkeypatch, _FakeDriver())
    return Neo4jClient()


@pytest.mark.asyncio
async def test_a_failed_connect_still_closes_the_pool(monkeypatch):
    """The pool is built in __init__ and __aexit__ does not run when __aenter__
    raises, so `async with` has to close it itself. Without this, a Neo4j that
    is simply down leaks a connection pool at every `async with Neo4jClient()`
    in the repo — which is most of scripts/ and both ingestion entry points."""
    driver = _FakeDriver(connect_error=ServiceUnavailable("down"))
    _install_driver(monkeypatch, driver)

    with pytest.raises(ServiceUnavailable):
        async with Neo4jClient():
            pass  # pragma: no cover - the body must not be reached

    assert driver.closed


@pytest.mark.asyncio
async def test_a_successful_context_closes_on_the_way_out(monkeypatch):
    driver = _FakeDriver()
    _install_driver(monkeypatch, driver)

    async with Neo4jClient() as db:
        assert db._driver is driver  # noqa: SLF001 — asserting the driver used
        assert not driver.closed

    assert driver.closed


@pytest.mark.asyncio
async def test_run_read_requests_read_routing_and_timeout(client):
    """The timeout must ride inside the Query, not as a driver kwarg.

    execute_query merges its **kwargs into the Cypher parameters, so a bare
    timeout= would arrive as an unused $timeout and cap nothing; only
    Query(text, timeout=...) reaches unit_of_work.
    """
    await client.run_read("MATCH (n) RETURN n", timeout_s=3.0, foo=1)
    call = client._driver.calls[0]  # noqa: SLF001 — asserting the driver call
    assert call["routing_"] == RoutingControl.READ
    assert isinstance(call["query"], Query)
    assert call["query"].text == "MATCH (n) RETURN n"
    assert call["query"].timeout == 3.0
    assert call["params"] == {"foo": 1}
    assert "timeout" not in call


@pytest.mark.asyncio
async def test_run_read_omits_timeout_when_unset(client):
    await client.run_read("MATCH (n) RETURN n")
    call = client._driver.calls[0]  # noqa: SLF001
    assert call["query"] == "MATCH (n) RETURN n"
    assert "timeout" not in call


@pytest.mark.asyncio
async def test_run_defaults_to_write_routing(client):
    """Ingestion writes through run(); it must NOT carry read routing."""
    await client.run("MATCH (n) RETURN n")
    assert "routing_" not in client._driver.calls[0]  # noqa: SLF001


@pytest.mark.asyncio
async def test_run_named_uses_the_read_only_path(client, monkeypatch):
    """The query service's every call goes through READ routing.

    It did not until 2026-08-21: run_named delegated to run(), which is
    WRITE-routed, so the API, the orchestrator and the catalogue all ran under
    write mode while run_read sat unused and the docs described a read-only
    query service. Every named template is non-mutating (the query-loader guard
    fails the build otherwise), so the routing mode can enforce what the guard
    asserts.
    """
    await client.run_named("healthcheck")
    call = client._driver.calls[0]  # noqa: SLF001 — asserting the driver call
    assert call["routing_"] == RoutingControl.READ


@pytest.mark.asyncio
async def test_run_named_can_carry_a_timeout(client):
    await client.run_named("healthcheck", timeout_s=2.5)
    call = client._driver.calls[0]  # noqa: SLF001
    assert isinstance(call["query"], Query)
    assert call["query"].timeout == 2.5
    # ...and the timeout is not smuggled in as a Cypher parameter.
    assert call["params"] == {}


@pytest.mark.asyncio
async def test_run_stays_the_write_path(client):
    """Ingestion and the builders write, and they call run() directly."""
    await client.run("MERGE (n:Thing {id: 1})")
    call = client._driver.calls[0]  # noqa: SLF001
    assert "routing_" not in call
