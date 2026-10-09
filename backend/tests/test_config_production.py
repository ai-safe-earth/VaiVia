"""Production refuses to boot with the gateway check switched off.

An empty GATEWAY_SHARED_SECRET disables the backend's only proof that a
request came through the gateway (api/middleware.py). In development that is
a convenience; in production it would be a public backend.
"""

import pytest
from pydantic import ValidationError

from core.config import Settings


def test_development_allows_an_empty_secret():
    assert Settings(_env_file=None).gateway_shared_secret == ""


def test_production_refuses_an_empty_secret():
    with pytest.raises(ValidationError, match="GATEWAY_SHARED_SECRET"):
        Settings(_env_file=None, vaivia_env="production")


def test_production_boots_with_a_secret():
    settings = Settings(
        _env_file=None, vaivia_env="production", gateway_shared_secret="s3cret"
    )
    assert settings.vaivia_env == "production"


def test_the_environment_is_read_from_the_process(monkeypatch):
    monkeypatch.setenv("VAIVIA_ENV", "production")
    monkeypatch.delenv("GATEWAY_SHARED_SECRET", raising=False)
    with pytest.raises(ValidationError, match="GATEWAY_SHARED_SECRET"):
        Settings(_env_file=None)


def test_an_unknown_environment_is_refused():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, vaivia_env="staging")
