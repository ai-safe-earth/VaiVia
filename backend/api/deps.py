"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Request

from graph.neo4j_client import Neo4jClient


def get_db(request: Request) -> Neo4jClient:
    """The app-lifetime Neo4j client. Overridden with a fake in tests."""
    return request.app.state.db


DbDep = Annotated[Neo4jClient, Depends(get_db)]
