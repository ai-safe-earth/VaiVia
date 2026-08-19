"""Pipeline settings and the PostGIS connection.

Reads PIPELINE_DATABASE_URL from the environment or the repo root .env — the
pipeline is a sibling of backend/, and the root .env is where the compose
variables already live.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[1]

# The working CRS pair (docs/plan.md): geometry is stored in 4326; length,
# buffer and area computations happen in UTM 32N.
CRS_STORAGE = 4326
CRS_METRIC = 32632

# The configured provinces, mirroring backend REGIONS. (min_lat, min_lon,
# max_lat, max_lon), the order the whole codebase uses.
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "Lecco": (45.8, 9.3, 46.0, 9.6),
    "Bergamo": (45.68, 9.55, 45.92, 9.85),
}


def database_url() -> str:
    url = os.environ.get("PIPELINE_DATABASE_URL")
    if url:
        return url
    env_file = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip()
    if "PIPELINE_DATABASE_URL" in values:
        return values["PIPELINE_DATABASE_URL"]
    password = values.get("POSTGIS_PASSWORD")
    if password:
        port = values.get("POSTGIS_PORT", "5433")
        user = values.get("POSTGIS_USER", "vaivia")
        db = values.get("POSTGIS_DB", "vaivia_geo")
        return f"postgresql://{user}:{password}@127.0.0.1:{port}/{db}"
    raise SystemExit(
        "PIPELINE_DATABASE_URL is not set (env or repo root .env), and no "
        "POSTGIS_PASSWORD to derive it from"
    )


def sqlalchemy_url() -> str:
    """The same connection, spelled for SQLAlchemy.

    SQLAlchemy resolves a bare `postgresql://` to psycopg2, which is not
    installed and would not be the right choice anyway — the pipeline uses
    psycopg 3. The `+psycopg` dialect selects it explicitly. GeoPandas builds a
    SQLAlchemy engine internally, so anything going through read_postgis needs
    this rather than database_url().
    """
    url = database_url()
    prefix = "postgresql://"
    if url.startswith(prefix):
        return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def connect() -> psycopg.Connection:
    """A plain connection. Loopback and dev-only, so no TLS decision needed here;
    if this store ever leaves localhost, port the host-based rule from
    backend/core/pg.py rather than adding sslmode to a URL."""
    return psycopg.connect(database_url())
