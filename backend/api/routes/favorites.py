"""Route favorites: saved catalogue routes, per user.

Storage and endpoints together, because they are one small feature. The rows
live in Supabase Postgres (infra/supabase/migrations/0003_favorites.sql) —
docs/social-layer.md's MongoDB reaction is deliberately NOT built here: that
design defers likes until the social feature is specified, and a personal
favorite is account data, which belongs where the ownership check is the
database's job. The browser reads the table directly under RLS if it ever
needs to; writes come only through here, as the owner, with the user id the
gateway verified — so every statement carries ``user_id = $1`` the way
chat/store.py does.

The favorite keys on ``route_id`` alone. :Route nodes are wiped and recreated
per export; the geometry-derived id is what persists (docs/route-document.md),
and a favorite whose route left the catalogue is reported as ``missing`` by
the list endpoint, never silently dropped.

Everything mounts under /routes, which the gateway already proxies —
unfavorite is a POST with ``{"on": false}`` because the gateway forwards only
GET and POST.
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import DbDep, UserDep

router = APIRouter(tags=["favorites"])


class FavoritesStore(Protocol):
    async def set(self, user_id: str, route_id: str, on: bool) -> None: ...

    async def ids(self, user_id: str) -> list[str]:
        """This user's favorite route ids, newest first."""
        ...


class InMemoryFavorites:
    """Dev/test double, mirroring chat.store.InMemoryStore: real behaviour,
    no persistence."""

    def __init__(self) -> None:
        # dict preserves insertion order, which is the saved order.
        self._by_user: dict[str, dict[str, None]] = {}

    async def set(self, user_id: str, route_id: str, on: bool) -> None:
        saved = self._by_user.setdefault(user_id, {})
        if on:
            saved.setdefault(route_id, None)
        else:
            saved.pop(route_id, None)

    async def ids(self, user_id: str) -> list[str]:
        return list(reversed(self._by_user.get(user_id, {})))


class PostgresFavorites:
    """The real store. Ownership lives in the SQL: the backend connects as
    the owner and bypasses RLS, so ``user_id = $1`` is the check."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def set(self, user_id: str, route_id: str, on: bool) -> None:
        async with self._pool.acquire() as conn:
            if on:
                # Idempotent: favoriting twice is one row, not an error.
                await conn.execute(
                    """
                    INSERT INTO route_favorites (user_id, route_id)
                    VALUES ($1::uuid, $2)
                    ON CONFLICT (user_id, route_id) DO NOTHING
                    """,
                    user_id,
                    route_id,
                )
            else:
                await conn.execute(
                    "DELETE FROM route_favorites "
                    "WHERE user_id = $1::uuid AND route_id = $2",
                    user_id,
                    route_id,
                )

    async def ids(self, user_id: str) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT route_id FROM route_favorites "
                "WHERE user_id = $1::uuid ORDER BY created_at DESC",
                user_id,
            )
        return [row["route_id"] for row in rows]


class FavoriteToggle(BaseModel):
    on: bool


class FavoriteState(BaseModel):
    route_id: str
    on: bool


class FavoritesList(BaseModel):
    """Hydrated favorites, in saved order (newest first), plus the ids whose
    route is no longer in the catalogue — shown, never silently dropped."""

    routes: list[dict[str, Any]]
    missing: list[str]


def _store(request: Request) -> FavoritesStore:
    return request.app.state.favorites


@router.get("/routes/favorites", response_model=FavoritesList)
async def list_favorites(
    request: Request, user_id: UserDep, db: DbDep
) -> FavoritesList:
    """One round trip: ids from Postgres, cards from the graph.

    The rows are the same shape search_loops returns (routes_by_ids shares
    its RETURN), so the client renders the same cards it renders for a
    search answer. Order is the user's saved order — the graph does not know
    it, so the re-sort happens here.
    """
    ids = await _store(request).ids(user_id)
    rows = await db.run_named("routes_by_ids", route_ids=ids) if ids else []
    by_id = {row["id"]: row for row in rows}
    return FavoritesList(
        routes=[by_id[i] for i in ids if i in by_id],
        missing=[i for i in ids if i not in by_id],
    )


@router.post("/routes/{route_id}/favorite", response_model=FavoriteState)
async def set_favorite(
    route_id: str,
    body: FavoriteToggle,
    request: Request,
    user_id: UserDep,
    db: DbDep,
) -> FavoriteState:
    """Idempotent toggle. Saving checks the route exists (an honest 404 beats
    a favorite that can never hydrate); unsaving does not — a route that left
    the catalogue must still be removable from the list."""
    if body.on:
        rows = await db.run_named("route_exists", route_id=route_id)
        if not rows:
            raise HTTPException(status_code=404, detail=f"unknown route {route_id!r}")
    await _store(request).set(user_id, route_id, body.on)
    return FavoriteState(route_id=route_id, on=body.on)
