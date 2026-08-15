"""Load named Cypher templates from graph/queries.cypher.

Templates are parameterized text — callers pass parameters, never interpolate.
Parsed once at import and cached; unknown names raise KeyError so a typo fails
loudly instead of running the wrong query.
"""

import re
from functools import lru_cache
from pathlib import Path

QUERIES_PATH = Path(__file__).resolve().parent / "queries.cypher"

_NAME_RE = re.compile(r"^//\s*name:\s*(\S+)\s*$", re.MULTILINE)


def parse(text: str) -> dict[str, str]:
    """Split a .cypher file into {name: body} on '// name: <x>' markers."""
    markers = list(_NAME_RE.finditer(text))
    templates: dict[str, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        body = text[marker.end() : end]
        # Drop comment-only lines so the sent query is exactly the statement.
        statement = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("//")
        ).strip()
        if not statement:
            raise ValueError(f"template {marker.group(1)!r} has no statement body")
        if marker.group(1) in templates:
            raise ValueError(f"duplicate template name: {marker.group(1)!r}")
        templates[marker.group(1)] = statement
    return templates


@lru_cache
def _templates() -> dict[str, str]:
    return parse(QUERIES_PATH.read_text(encoding="utf-8"))


def get_query(name: str) -> str:
    templates = _templates()
    try:
        return templates[name]
    except KeyError:
        raise KeyError(
            f"unknown query template {name!r}; available: {sorted(templates)}"
        ) from None


def query_names() -> list[str]:
    return sorted(_templates())
