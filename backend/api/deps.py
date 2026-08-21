"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from core.embeddings import Embedder
from graph.neo4j_client import Neo4jClient


def get_db(request: Request) -> Neo4jClient:
    """The app-lifetime Neo4j client. Overridden with a fake in tests."""
    return request.app.state.db


def get_embedder(request: Request) -> Embedder:
    """The app-lifetime embeddings client. Overridden with a fake in tests."""
    return request.app.state.embedder


def get_user_id(x_user_id: str = Header(default="")) -> str:
    """The verified user, as the gateway forwarded them.

    The backend never sees a token: the gateway verifies the Supabase JWT and
    injects x-user-id alongside the shared secret. An empty header means the
    request skipped the gateway's authenticate step, which no user-scoped
    endpoint accepts.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=401, detail="missing user identity from gateway"
        )
    return x_user_id


DbDep = Annotated[Neo4jClient, Depends(get_db)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
UserDep = Annotated[str, Depends(get_user_id)]
