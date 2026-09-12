"""SSE chat endpoint.

The gateway has already verified the JWT and passes the user id in X-User-Id —
the backend never parses tokens. Responses stream as Server-Sent Events so the
frontend renders tokens as they arrive.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from api.deps import DbDep, UserDep
from chat.orchestrator import ChatEvent, ChatOrchestrator, QuotaExceeded
from core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class NearPoint(BaseModel):
    """The user's location for "from here" asks — TYPED, validated to the
    coverage regions, attached to the plan AFTER intent extraction. The LLM
    never sees a coordinate (docs/route-design.md); a test pins that the
    extraction call carries none."""

    lat: float
    lon: float

    @model_validator(mode="after")
    def _inside_coverage(self) -> "NearPoint":
        for _name, (lat_min, lon_min, lat_max, lon_max) in get_settings().region_list:
            if lat_min <= self.lat <= lat_max and lon_min <= self.lon <= lon_max:
                return self
        raise ValueError("near is outside the covered regions")


class OutingChip(BaseModel):
    """A refinement tap: a typed delta onto the conversation's standing
    outing, merged in Python with NO model call. Only these fields exist —
    a chip cannot carry a query, an id, a coordinate or a weight any more
    than the intent it refines can."""

    max_hours: float | None = Field(default=None, ge=0, le=24)
    max_distance_km: float | None = Field(default=None, ge=0, le=200)
    max_ascent_m: int | None = Field(default=None, ge=0, le=5000)
    surface_exclusions: list[Literal["asphalt", "paved", "gravel"]] | None = None
    setting: Literal["nature", "mixed", "town"] | None = None

    def delta(self) -> dict:
        """Only the fields the tap actually set."""
        return self.model_dump(exclude_none=True)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    near: NearPoint | None = None
    chip: OutingChip | None = None


def _sse(event: ChatEvent) -> str:
    return f"event: {event.event}\ndata: {json.dumps(event.data, default=str)}\n\n"


async def _stream(events: AsyncIterator[ChatEvent]) -> AsyncIterator[str]:
    """Errors mid-stream become an SSE error event: headers are already sent, so
    an HTTP status can no longer be changed."""
    try:
        async for event in events:
            yield _sse(event)
    except QuotaExceeded as exc:
        yield _sse(
            ChatEvent(
                "error",
                {
                    "error": "quota_exceeded",
                    "message": "Daily usage limit reached. It resets at midnight UTC.",
                    "used": exc.used,
                    "limit": exc.limit,
                },
            )
        )
    except Exception:
        logger.exception("chat stream failed")
        yield _sse(
            ChatEvent(
                "error",
                {"error": "internal_error", "message": "Something went wrong."},
            )
        )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    db: DbDep,
    user_id: UserDep,
) -> StreamingResponse:
    state = http_request.app.state
    if getattr(state, "outing_docs", None) is None:
        state.outing_docs = {}
    orchestrator = ChatOrchestrator(
        db=db,
        llm=state.llm,
        store=state.store,
        embedder=getattr(state, "embedder", None),
        planner=getattr(state, "planner", None),
        outing_docs=state.outing_docs,
    )

    return StreamingResponse(
        _stream(
            orchestrator.run(
                user_id=user_id,
                message=request.message,
                conversation_id=request.conversation_id,
                near=((request.near.lat, request.near.lon) if request.near else None),
                chip=(request.chip.delta() if request.chip else None),
            )
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
