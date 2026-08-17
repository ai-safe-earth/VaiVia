"""Text embeddings: one small protocol, one OpenAI-backed implementation.

Used from two places with different lifecycles — the offline embedding job
(scripts/embed_trails.py) and the semantic-search endpoint — so it lives in
core/ rather than chat/.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from core.config import get_settings


class Embedder(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """text-embedding-3-small by default; model and dimensions from settings."""

    def __init__(self, api_key: str | None = None) -> None:
        from openai import AsyncOpenAI  # lazy so tests need no SDK config

        settings = get_settings()
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimensions,
        )
        # The API may reorder nothing, but index explicitly rather than trust it.
        vectors: list[list[float]] = [[] for _ in texts]
        for item in response.data:
            vectors[item.index] = item.embedding
        return vectors


def embedding_input(
    description: str | None,
    landscape_description: str | None,
    difficulty_notes: str | None,
    activity: str | None = None,
    difficulty: str | None = None,
    best_seasons: list[str] | None = None,
    pois: list[dict] | None = None,
) -> str:
    """The owner-ratified embedding input (extended 2026-08-16): the three
    prose fields, then activity/difficulty, seasons, and the POIs along the
    way — so themes like "swim spot" or "hut" match semantically even where
    POI edges are sparse."""
    parts = [description, landscape_description, difficulty_notes]
    if activity or difficulty:
        parts.append(
            " ".join(p for p in (f"{difficulty or ''}".strip(), activity) if p)
        )
    if best_seasons:
        parts.append("Best seasons: " + ", ".join(best_seasons) + ".")
    if pois:
        labels = []
        for poi in pois:
            name, kind = poi.get("name"), poi.get("type")
            if not kind:
                continue
            labels.append(f"{name} ({kind})" if name else str(kind))
        if labels:
            parts.append("Along the way: " + ", ".join(labels) + ".")
    return "\n".join(p.strip() for p in parts if p and p.strip())


def input_sha(text: str) -> str:
    """Content hash stored on the node so re-runs skip unchanged trails."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
