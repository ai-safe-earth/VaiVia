"""Load named Cypher templates from graph/queries.cypher.

Templates are parameterized text — callers pass parameters, never interpolate.
Parsed once at import and cached; unknown names raise KeyError so a typo fails
loudly instead of running the wrong query.

**Fragments.** A `// fragment: <name>` block is a reusable Cypher chunk that is
NOT a runnable template — it exists only to be spliced into templates via a
`// include: <name>` line. This exists so the loop-candidate filter block lives
in exactly one place: `search_loops` and `estimate_loops` both include it, so
they cannot drift, and a count can never disagree with the search it counts.
Fragments are resolved BEFORE comment lines are stripped (an `// include:` line
is itself a comment), and a fragment may not include another (depth 1) — the
one filter block does not need nesting, and forbidding it keeps this simple.
"""

import re
from functools import lru_cache
from pathlib import Path

QUERIES_PATH = Path(__file__).resolve().parent / "queries.cypher"

_NAME_RE = re.compile(r"^//\s*name:\s*(\S+)\s*$", re.MULTILINE)
_FRAGMENT_RE = re.compile(r"^//\s*fragment:\s*(\S+)\s*$", re.MULTILINE)
_MARKER_RE = re.compile(r"^//\s*(?:name|fragment):\s*\S+\s*$", re.MULTILINE)
_INCLUDE_RE = re.compile(r"^//\s*include:\s*(\S+)\s*$", re.MULTILINE)


def _strip_comment_lines(body: str) -> str:
    return "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//")
    ).strip()


def _blocks(text: str) -> list[tuple[str, str, str]]:
    """Every '// name:' / '// fragment:' block as (kind, name, raw_body)."""
    markers = list(_MARKER_RE.finditer(text))
    out: list[tuple[str, str, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        header = marker.group(0)
        kind = "fragment" if _FRAGMENT_RE.match(header) else "name"
        name = header.split(":", 1)[1].strip()
        out.append((kind, name, text[marker.end() : end]))
    return out


def parse(text: str) -> dict[str, str]:
    """Split a .cypher file into {name: body}, resolving fragment includes.

    Fragments are collected first and never returned; a runnable template's
    `// include: <fragment>` lines are replaced with the fragment body, then
    comment lines are stripped so the sent query is exactly the statement.
    """
    fragments: dict[str, str] = {}
    for kind, name, raw in _blocks(text):
        if kind != "fragment":
            continue
        if name in fragments:
            raise ValueError(f"duplicate fragment name: {name!r}")
        if _INCLUDE_RE.search(raw):
            raise ValueError(
                f"fragment {name!r} includes another fragment (max depth 1)"
            )
        fragments[name] = _strip_comment_lines(raw)

    def resolve(raw: str, owner: str) -> str:
        def sub(match: re.Match[str]) -> str:
            frag = match.group(1)
            if frag not in fragments:
                raise ValueError(
                    f"template {owner!r} includes unknown fragment {frag!r}"
                )
            return fragments[frag]

        return _INCLUDE_RE.sub(sub, raw)

    templates: dict[str, str] = {}
    for kind, name, raw in _blocks(text):
        if kind != "name":
            continue
        statement = _strip_comment_lines(resolve(raw, name))
        if not statement:
            raise ValueError(f"template {name!r} has no statement body")
        if name in templates:
            raise ValueError(f"duplicate template name: {name!r}")
        templates[name] = statement
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
