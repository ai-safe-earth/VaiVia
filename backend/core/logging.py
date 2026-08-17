"""Structured JSON logging with request-id correlation.

The gateway generates the request id and forwards it as X-Request-ID; the
backend echoes it on the response and stamps it on every log line, so one id
traces a request across gateway, backend, and (Phase 4) the LLM call.
"""

import json
import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        # Anything passed via logger.info(..., extra={...}) rides along.
        payload.update(
            {
                k: v
                for k, v in record.__dict__.items()
                if k not in _RESERVED and k != "taskName"
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "info") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
