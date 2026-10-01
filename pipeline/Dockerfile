# The jobs image: the geodata pipeline (and, later, the self-improving agent)
# run on the server against its PostGIS. Its own uv project — never the
# backend's environment (CLAUDE.md). Build from the REPO ROOT:
#   docker build -f pipeline/Dockerfile -t vaivia-jobs .
#
# One-shot by design: `docker compose run --rm jobs python migrate.py`,
# `... python -m export.pack`, and so on. PIPELINE_DATABASE_URL comes from
# the environment; packs and documents are written to mounted volumes.

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /usr/local/bin/uv

# osmium's wheel links against the system expat, which slim does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib

COPY shared/routes /app/shared/routes
COPY pipeline/pyproject.toml pipeline/uv.lock /app/pipeline/
WORKDIR /app/pipeline
RUN uv sync --frozen --no-dev

COPY pipeline /app/pipeline

RUN useradd --uid 10001 --no-create-home app
USER app

CMD ["python", "migrate.py", "--dry-run"]
