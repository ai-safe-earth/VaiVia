"""Strip links out of the model's answer, in Python rather than by asking.

The answer prompt forbids links, and a live smoke on 2026-08-21 showed why a
prompt is not enough: asked for a loop hike, the model linked every route name
to ``https://www.trailforks.com`` -- a domain no VaiVia result comes from
(docs/licensing.md), pointed at OSM-derived routes it would have misattributed.

This is the same doctrine as the Cypher boundary: a rule that matters is
enforced by code, not left to the model's good behaviour. The cards on screen
carry the sources; the prose carries none.

The stream is the awkward part -- ``[Name](url)`` arrives in pieces and a
regex over one chunk sees only fragments -- so :func:`strip_links_stream` holds
back the tail that could still grow into a link and flushes it as soon as it
cannot.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

#: ``[label](url)`` -> ``label``. The label keeps the route's name, which the
#: answer needs so its prose and the cards on screen agree.
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*<?[^)\s]*[^)]*\)")

#: A bare URL the model typed out. Scheme-ful or www-prefixed only: a rule that
#: also ate ``trailforks.com`` would eat ``Monte Misma, 1.161 m.s.l.m.`` too.
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)

_EMPTY_PARENS = re.compile(r"\(\s*\)")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")


def unlink(text: str) -> str:
    """Remove every link from ``text``, keeping the words around it readable."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    text = _EMPTY_PARENS.sub("", text)
    text = _RUN_OF_SPACES.sub(" ", text)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text)


def _hold_from(buffer: str) -> int:
    """Index from which ``buffer`` could still grow into a link.

    Two things can be half-arrived: an opening ``[`` whose ``](url)`` has not
    landed yet, and the final word, which the next chunk may extend into a URL.
    Whichever starts earlier is where holding begins.
    """
    # The last word is unfinished until whitespace follows it.
    last_break = max(buffer.rfind(" "), buffer.rfind("\n"), buffer.rfind("\t"))
    hold = min(len(buffer), last_break + 1)

    # ...but a link holds spaces inside it, so a cut that lands in the middle
    # of a COMPLETE link would emit its label and strip only the tail. Such a
    # link is already safe to rewrite, so release it whole.
    for match in _MARKDOWN_LINK.finditer(buffer):
        if match.start() < hold < match.end():
            hold = match.end()

    # An unclosed '[': a bracket whose '](url)' has not landed yet.
    opening = buffer.rfind("[")
    while opening != -1:
        if not _MARKDOWN_LINK.search(buffer, opening):
            hold = min(hold, opening)
            break
        opening = buffer.rfind("[", 0, opening)

    return hold


async def strip_links_stream(deltas: AsyncIterator[str]) -> AsyncIterator[str]:
    """Pass the answer through, minus any link, without breaking streaming.

    Text is released as soon as it can no longer become part of a link, so the
    reader sees the answer arrive word by word rather than all at once.
    """
    pending = ""
    async for delta in deltas:
        pending += delta
        cut = _hold_from(pending)
        if cut:
            emitted = unlink(pending[:cut])
            pending = pending[cut:]
            if emitted:
                yield emitted
    if pending:
        tail = unlink(pending)
        if tail:
            yield tail
