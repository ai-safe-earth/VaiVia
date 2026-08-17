"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Request

from core.embeddings import Embedder
from graph.neo4j_client import Neo4jClient


def get_db(request: Request) -> Neo4jClient:
    """The app-lifetime Neo4j client. Overridden with a fake in tests."""
    return request.app.state.db


def get_embedder(request: Request) -> Embedder:
    """The app-lifetime embeddings client. Overridden with a fake in tests."""
    return request.app.state.embedder


DbDep = Annotated[Neo4jClient, Depends(get_db)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
