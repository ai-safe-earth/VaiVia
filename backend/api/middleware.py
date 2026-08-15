"""Request-id propagation and gateway-trust enforcement.

The backend is never publicly exposed (see docs/plan.md): the Fastify gateway is
the only ingress and proves it by sending X-Gateway-Secret. Anything else gets
401 before touching the database.
"""

import logging
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from core.config import get_settings
from core.logging import request_id_var

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
GATEWAY_SECRET_HEADER = "X-Gateway-Secret"  # noqa: S105 — header name, not a secret
PUBLIC_PATHS = frozenset({"/healthz", "/docs", "/openapi.json", "/redoc"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Adopt (or mint) a request id, log the outcome, echo the id back."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )
        return response


class GatewayTrustMiddleware(BaseHTTPMiddleware):
    """Reject requests that did not come through the gateway."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        secret = get_settings().gateway_shared_secret
        if secret and request.url.path not in PUBLIC_PATHS:
            presented = request.headers.get(GATEWAY_SECRET_HEADER, "")
            if presented != secret:
                logger.warning(
                    "rejected non-gateway request",
                    extra={
                        "path": request.url.path,
                        "client": request.client.host if request.client else None,
                    },
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "requests must come through the gateway"},
                )
        return await call_next(request)
