"""LLM client: structured intent extraction and streamed answer generation.

The Protocol is the seam every test uses — the whole chat pipeline runs offline
against a stub, so no test ever spends money or needs the network.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from chat.intents import PlanEnvelope, to_strict_schema
from chat.prompts import ANSWER_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class PlanResult:
    envelope: PlanEnvelope
    usage: Usage


class LLMClient(Protocol):
    async def extract_plan(
        self, message: str, history: list[dict[str, str]]
    ) -> PlanResult: ...

    def stream_answer(
        self, message: str, results_json: str, history: list[dict[str, str]]
    ) -> AsyncIterator[str]: ...

    def last_answer_usage(self) -> Usage: ...


class OpenAIClient:
    """OpenAI-backed implementation using strict structured outputs."""

    def __init__(self, api_key: str | None = None) -> None:
        from openai import AsyncOpenAI  # imported lazily so tests need no SDK config

        settings = get_settings()
        self._client = AsyncOpenAI(api_key=api_key or settings.openai_api_key)
        self._intent_model = settings.intent_model
        self._answer_model = settings.answer_model
        self._answer_usage = Usage()

    async def extract_plan(
        self, message: str, history: list[dict[str, str]]
    ) -> PlanResult:
        response = await self._client.chat.completions.create(
            model=self._intent_model,
            messages=[
                {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                *history,
                {"role": "user", "content": message},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "plan_envelope",
                    "strict": True,
                    "schema": to_strict_schema(PlanEnvelope),
                },
            },
            temperature=0,
        )
        raw = response.choices[0].message.content or "{}"
        usage = Usage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )
        return PlanResult(envelope=PlanEnvelope.model_validate_json(raw), usage=usage)

    async def stream_answer(
        self, message: str, results_json: str, history: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        self._answer_usage = Usage()
        stream = await self._client.chat.completions.create(
            model=self._answer_model,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                *history,
                {"role": "user", "content": message},
                {"role": "user", "content": f"RESULTS:\n{results_json}"},
            ],
            stream=True,
            stream_options={"include_usage": True},
            temperature=0.4,
        )
        async for chunk in stream:
            if chunk.usage is not None:
                self._answer_usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                )
            for choice in chunk.choices:
                delta = choice.delta.content
                if delta:
                    yield delta

    def last_answer_usage(self) -> Usage:
        return self._answer_usage


def results_to_json(payload: Any) -> str:
    """Serialize graph results for the answer prompt (data, never instructions)."""
    return json.dumps(payload, ensure_ascii=False, default=str)
