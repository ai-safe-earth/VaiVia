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

#: A bare URL the model typed out, scheme-ful or www-prefixed.
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)

#: ...and the same thing with neither, which the rule above let through. The
#: ban is on links, and ``trailforks.com/trails/lecco`` is one a walker can
#: type in whatever the model left off. Two shapes only, so ordinary prose
#: survives: a domain under a GENERIC TLD, or any domain carrying a path.
#: ``Monte Misma, 1.161 m.s.l.m.`` is neither -- and the two-letter country
#: codes stay out of the generic set, because a missing space after a full
#: stop ("steep.It flattens") would otherwise read as one.
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_PATH = r"/[^\s<>()\[\]]*"
_BARE_DOMAIN = re.compile(
    rf"(?<![\w.@/])(?:{_LABEL}\.)+(?:com|org|net|io|app|dev|info)\b(?:{_PATH})?"
    rf"|(?<![\w.@/])(?:{_LABEL}\.)+[a-z]{{2,}}{_PATH}",
    re.IGNORECASE,
)

_EMPTY_PARENS = re.compile(r"\(\s*\)")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?])")


def find_link(text: str) -> str | None:
    """The first link-shaped thing in ``text``, or None.

    Detection half of :func:`unlink`, for callers that must report rather than
    repair — the eval checks the RAW answer with this, because production has
    already stripped what the model tried to link (scripts/eval_golden.py).
    """
    for pattern in (_MARKDOWN_LINK, _BARE_URL, _BARE_DOMAIN):
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def unlink(text: str) -> str:
    """Remove every link from ``text``, keeping the words around it readable."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    text = _BARE_DOMAIN.sub("", text)
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
            if _link_still_possible(buffer, opening):
                hold = min(hold, opening)
                break
        opening = buffer.rfind("[", 0, opening)

    return hold


def _link_still_possible(buffer: str, opening: int) -> bool:
    r"""Could the '[' at ``opening`` still grow into a link, or is it just a '['?

    Holding on a bracket that can never close is what turns a streamed answer
    into one late blob: a '[1]' citation or an aside like '[T1 to T3 apply'
    would pin the hold and buffer every later token to end of stream. Two
    things rule a link out for good, both read off _MARKDOWN_LINK: a newline in
    the label, which its ``[^\]\n]*`` class forbids, and a ']' followed by
    anything other than the '(' the pattern requires next.
    """
    rest = buffer[opening + 1 :]
    close = rest.find("]")
    label = rest if close == -1 else rest[:close]
    if "\n" in label:
        return False
    if close == -1:
        return True  # the ']' may still be on its way
    return rest[close + 1 : close + 2] in ("", "(")


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
