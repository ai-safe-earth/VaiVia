"""Gateway-trust enforcement, request-id propagation, and health reporting."""

import pytest

from core.config import get_settings


@pytest.fixture
def with_gateway_secret():
    """Enable the gateway check for one test (settings are cached)."""
    settings = get_settings()
    original = settings.gateway_shared_secret
    settings.gateway_shared_secret = "test-secret"  # noqa: S105 — test fixture
    yield "test-secret"
    settings.gateway_shared_secret = original


def test_request_id_is_echoed_back(client, db):
    db.when("search_trails", [])
    response = client.post(
        "/trails/search", json={}, headers={"X-Request-ID": "abc-123"}
    )
    assert response.headers["X-Request-ID"] == "abc-123"


def test_request_id_is_minted_when_absent(client, db):
    db.when("search_trails", [])
    response = client.post("/trails/search", json={})
    assert len(response.headers["X-Request-ID"]) >= 32


def test_backend_rejects_requests_without_gateway_secret(
    client, db, with_gateway_secret
):
    db.when("search_trails", [])
    response = client.post("/trails/search", json={})
    assert response.status_code == 401
    assert "gateway" in response.json()["detail"]


def test_backend_rejects_wrong_gateway_secret(client, db, with_gateway_secret):
    response = client.post(
        "/trails/search", json={}, headers={"X-Gateway-Secret": "wrong"}
    )
    assert response.status_code == 401


def test_backend_accepts_correct_gateway_secret(client, db, with_gateway_secret):
    db.when("search_trails", [])
    response = client.post(
        "/trails/search",
        json={},
        headers={"X-Gateway-Secret": with_gateway_secret},
    )
    assert response.status_code == 200


def test_healthz_is_public_even_with_secret_configured(client, db, with_gateway_secret):
    assert client.get("/healthz").status_code == 200


def test_healthz_reports_up(client, db):
    body = client.get("/healthz").json()
    assert body == {"status": "ok", "database": "up"}


def test_healthz_reports_degraded_when_database_is_down(client, db):
    db.fail_with = RuntimeError("connection refused")
    body = client.get("/healthz").json()
    assert body == {"status": "degraded", "database": "down"}
