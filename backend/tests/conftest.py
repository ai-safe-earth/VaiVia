"""Test fixtures: an in-process app with a fake graph client.

No Neo4j required — the fake records which named template each endpoint ran and
with which parameters, which is exactly the contract we want to pin down.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from graph.query_loader import get_query


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


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def client(db: FakeDb) -> TestClient:
    # Import here so app creation happens after fixtures are ready.
    from api.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.state.db = db
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
