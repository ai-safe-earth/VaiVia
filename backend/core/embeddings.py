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
) -> str:
    """The owner-ratified embedding input (docs/plan.md Phase 3):
    description + landscape_description + difficulty_notes, in that order."""
    parts = [description, landscape_description, difficulty_notes]
    return "\n".join(p.strip() for p in parts if p and p.strip())


def input_sha(text: str) -> str:
    """Content hash stored on the node so re-runs skip unchanged trails."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
