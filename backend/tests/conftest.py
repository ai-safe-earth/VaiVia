"""Test fixtures: an in-process app with a fake graph client.

No Neo4j required — the fake records which named template each endpoint ran and
with which parameters, which is exactly the contract we want to pin down.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from core.config import get_settings
from graph.query_loader import get_query


@pytest.fixture(autouse=True)
def isolated_settings() -> Any:
    """Pin the environment-sensitive settings so the suite is hermetic.

    Both of these are read from ``backend/.env`` in normal use, which made test
    outcomes depend on the developer's local file: a populated
    ``GATEWAY_SHARED_SECRET`` turns on the gateway guard and 401s every request
    that does not send the header, and a populated ``DATABASE_URL`` makes the
    app open a real Postgres pool at startup. Tests that want either behaviour
    set it themselves.
    """
    settings = get_settings()
    saved = (settings.gateway_shared_secret, settings.database_url)
    settings.gateway_shared_secret = ""
    settings.database_url = ""
    yield
    settings.gateway_shared_secret, settings.database_url = saved


class FakeDb:
    """Stands in for Neo4jClient. Returns queued rows per template name."""

    def __init__(self) -> None:
        self.responses: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_with: Exception | None = None

    def when(self, name: str, rows: list[dict[str, Any]]) -> None:
        self.responses[name] = rows

    async def run_named(self, name: str, /, **params: Any) -> list[dict[str, Any]]:
        if self.fail_with is not None:
            raise self.fail_with
        get_query(name)  # fail loudly if an endpoint names a missing template
        self.calls.append((name, params))
        return self.responses.get(name, [])

    def params_for(self, name: str) -> dict[str, Any]:
        return next(params for called, params in self.calls if called == name)


class FakeEmbedder:
    """Deterministic embedder: unit vector per call, records inputs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0] + [0.0] * 1535 for _ in texts]


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def client(db: FakeDb, embedder: FakeEmbedder) -> TestClient:
    # Import here so app creation happens after fixtures are ready.
    from api.deps import get_embedder
    from api.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.state.db = db
    app.state.embedder = embedder
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


TRAIL_ROW = {
    "id": "tf_001",
    "name": "Lago Loop",
    "activity": "mtb",
    "difficulty": "Intermediate",
    "difficulty_level": 2,
    "difficulty_notes": "Two short rock gardens; walkable.",
    "landscape_description": "Lakeside gravel into chestnut forest.",
    "total_distance_m": 12400.0,
    "elevation_gain_m": 420.0,
    "elevation_loss_m": 420.0,
    "duration_hike_min": None,
    "duration_mtb_min": 88,
    "best_seasons": ["spring", "summer", "autumn"],
    "seasonal_hazards": ["mud_after_rain"],
    "pois": [{"name": "Lago di Como", "type": "lake"}],
}
