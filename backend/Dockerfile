# The backend: FastAPI chat orchestration + the pack planner.
# Build from the REPO ROOT — shared/routes is a path dependency:
#   docker build -f backend/Dockerfile -t vaivia-backend .
#
# No secrets and no data in the image: settings come from the environment
# (compose env_file + environment), the pack and the route documents are
# mounted (PACK_DIR, ROUTE_DOCUMENTS_DIR). Scripts ship in the image, so
# `docker compose exec backend python -m scripts.<x>` is the server console.

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Dependencies first, so a code change does not reinstall them. The path
# dependency has to be in place before the sync that installs it.
COPY shared/routes /app/shared/routes
COPY backend/pyproject.toml backend/uv.lock /app/backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-dev

COPY backend /app/backend

RUN useradd --uid 10001 --no-create-home app
USER app

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
