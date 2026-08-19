"""FastAPI application — internal service behind the Fastify gateway.

Run from backend/:  uv run uvicorn api.main:app --reload
Never publish this port: the gateway is the only public ingress (docs/plan.md).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.middleware import GatewayTrustMiddleware, RequestContextMiddleware
from api.models import HealthResponse
from api.routes import chat, routing, trails
from core.config import get_settings
from core.logging import configure_logging
from core.pg import asyncpg_ssl
from graph.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Tests inject a fake client before startup; only own the connection if we
    # opened it ourselves.
    owns_client = getattr(app.state, "db", None) is None
    if owns_client:
        app.state.db = Neo4jClient()
        await app.state.db.connect()
        logger.info("backend started", extra={"neo4j_uri": settings.neo4j_uri})

    if getattr(app.state, "llm", None) is None:
        from chat.llm import OpenAIClient

        app.state.llm = OpenAIClient()
    if getattr(app.state, "embedder", None) is None:
        from core.embeddings import OpenAIEmbedder

        app.state.embedder = OpenAIEmbedder()
    owns_pool = False
    if getattr(app.state, "store", None) is None:
        # Postgres store is wired when DATABASE_URL is set; without it, history
        # and the cost ledger live in memory (dev only — quotas reset on restart).
        if settings.database_url:
            import asyncpg

            from chat.store import PostgresStore

            # statement_cache_size=0 keeps this working if DATABASE_URL is ever
            # pointed at the pooler's transaction mode (6543), where prepared
            # statements are not safe across pooled connections.
            app.state.pg_pool = await asyncpg.create_pool(
                settings.database_url,
                ssl=asyncpg_ssl(settings.database_url),
                statement_cache_size=0,
                min_size=1,
                max_size=5,
            )
            owns_pool = True
            app.state.store = PostgresStore(app.state.pg_pool)
            logger.info("chat history and quotas backed by Postgres")
        else:
            from chat.store import InMemoryStore

            logger.warning("DATABASE_URL unset — chat history and quotas are in-memory")
            app.state.store = InMemoryStore()

    try:
        yield
    finally:
        if owns_pool:
            await app.state.pg_pool.close()
        if owns_client:
            await app.state.db.close()


app = FastAPI(
    title="VaiVia backend",
    version="0.1.0",
    summary="Graph query service for trail search and routing",
    lifespan=lifespan,
)

# Order matters: the outermost middleware is added last, so request-id context
# exists before the gateway check can log a rejection.
app.add_middleware(GatewayTrustMiddleware)
app.add_middleware(RequestContextMiddleware)

app.include_router(trails.router)
app.include_router(routing.router)
app.include_router(chat.router)


@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
async def healthz() -> HealthResponse:
    """Liveness + database reachability. Public (no gateway secret required)."""
    try:
        await app.state.db.run_named("healthcheck")
    except Exception:
        logger.exception("healthcheck failed")
        return HealthResponse(status="degraded", database="down")
    return HealthResponse(status="ok", database="up")
