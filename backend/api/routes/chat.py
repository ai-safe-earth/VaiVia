"""SSE chat endpoint.

The gateway has already verified the JWT and passes the user id in X-User-Id —
the backend never parses tokens. Responses stream as Server-Sent Events so the
frontend renders tokens as they arrive.
"""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import DbDep
from chat.orchestrator import ChatEvent, ChatOrchestrator, QuotaExceeded

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


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
    x_user_id: str = Header(default=""),
) -> StreamingResponse:
    if not x_user_id:
        raise HTTPException(
            status_code=401, detail="missing user identity from gateway"
        )

    state = http_request.app.state
    orchestrator = ChatOrchestrator(
        db=db,
        llm=state.llm,
        store=state.store,
        embedder=getattr(state, "embedder", None),
    )

    return StreamingResponse(
        _stream(
            orchestrator.run(
                user_id=x_user_id,
                message=request.message,
                conversation_id=request.conversation_id,
            )
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
