"""The answer carries no links, however the stream chops them up.

The case these pin is the one a live smoke found on 2026-08-21: asked for a
loop hike, the answer model linked every route name to ``trailforks.com``, a
domain no VaiVia result comes from. The prompt forbids links; this is the code
that makes the ban true.
"""

from collections.abc import AsyncIterator

import pytest

from chat.sanitize import strip_links_stream, unlink


async def _stream(chunks: list[str]) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


async def _collect(chunks: list[str]) -> str:
    return "".join([part async for part in strip_links_stream(_stream(chunks))])


def test_markdown_link_keeps_its_label() -> None:
    text = "The loop to [Corno dell'Arco](https://www.trailforks.com) is 11 km."
    assert unlink(text) == "The loop to Corno dell'Arco is 11 km."


def test_bare_url_is_removed_without_leaving_debris() -> None:
    assert unlink("See https://www.trailforks.com for more.") == "See for more."
    assert unlink("Details (www.trailforks.com) here.") == "Details here."


def test_text_that_merely_looks_technical_survives() -> None:
    # A rule that also ate bare domains would eat an altitude, a file name and
    # a grade along with them.
    kept = "Monte Misma, 1.161 m.s.l.m., graded T3 (sac_scale=3)."
    assert unlink(kept) == kept


@pytest.mark.asyncio
async def test_stream_strips_a_link_split_across_chunks() -> None:
    # How it actually arrives from the model: one token at a time, so no single
    # chunk contains the whole link.
    chunks = [
        "The loop to [Corno",
        " dell'Arco](https",
        "://www.trail",
        "forks.com)",
        " is 11 km.",
    ]
    assert await _collect(chunks) == "The loop to Corno dell'Arco is 11 km."


@pytest.mark.asyncio
async def test_stream_splits_between_every_character() -> None:
    text = "Try [Monte Misma](https://www.trailforks.com) next."
    assert await _collect(list(text)) == "Try Monte Misma next."


@pytest.mark.asyncio
async def test_stream_passes_ordinary_prose_through_unchanged() -> None:
    chunks = [
        "A 10.8 km loop",
        " to Monte Misma,",
        " 930 m of climb.\n",
        "Start at Albino.",
    ]
    assert await _collect(chunks) == "".join(chunks)


@pytest.mark.asyncio
async def test_unclosed_bracket_still_reaches_the_reader() -> None:
    # A '[' that never closes holds text back; the flush at end of stream is
    # what stops it being swallowed.
    assert (
        await _collect(["Grades [T1", " to T3 on this one"])
        == "Grades [T1 to T3 on this one"
    )


async def _parts(chunks: list[str]) -> list[str]:
    return [part async for part in strip_links_stream(_stream(chunks))]


@pytest.mark.asyncio
async def test_a_bracket_that_cannot_close_does_not_freeze_the_stream() -> None:
    """A '[1]'-style citation must not pin the hold to end of stream.

    The regression: ``_hold_from`` held on any '[' without a '](url)' after it,
    so one non-link bracket buffered the whole rest of the answer and the
    reader got a late blob instead of a stream.
    """
    chunks = ["See [1] ", "and the answer ", "keeps arriving ", "word by word."]
    parts = await _parts(chunks)
    assert "".join(parts) == "".join(chunks)
    # Each whitespace-terminated chunk left as it arrived, not at the flush.
    assert parts[:3] == chunks[:3]


@pytest.mark.asyncio
async def test_a_newline_in_the_label_rules_the_link_out() -> None:
    # _MARKDOWN_LINK's label class is [^\]\n]*, so a newline settles it: this
    # '[' can never become a link and must stop holding.
    chunks = ["Grades [T1 to T3 apply\n", "on the north face ", "of the ridge."]
    parts = await _parts(chunks)
    assert "".join(parts) == "".join(chunks)
    assert parts[:2] == chunks[:2]


@pytest.mark.asyncio
async def test_a_bracket_that_could_still_close_is_held() -> None:
    # The other half of the same rule: an open label is still a possible link,
    # so nothing from '[' onwards may be emitted yet.
    parts = await _parts(
        ["The loop to [Corno", " dell'Arco](https://www.trailforks.com)", " is 11 km."]
    )
    assert parts[0] == "The loop to "
    assert "".join(parts) == "The loop to Corno dell'Arco is 11 km."


@pytest.mark.asyncio
async def test_a_closed_label_followed_by_a_paren_is_held() -> None:
    # ']' then '(' is exactly the shape _MARKDOWN_LINK wants next, so the
    # bracket keeps holding until the URL lands.
    parts = await _parts(["Try [Monte Misma](", "https://www.trailforks.com) next."])
    assert parts[0] == "Try "
    assert "".join(parts) == "Try Monte Misma next."


def test_a_link_without_a_scheme_is_still_a_link() -> None:
    # The ban is on links, and a walker can type a domain the model wrote
    # without http:// just as easily. trailforks.com in particular must never
    # appear: no VaiVia result comes from there (docs/licensing.md).
    assert unlink("See trailforks.com for more.") == "See for more."
    assert unlink("Try trailforks.com/trails/lecco next.") == "Try next."
    # The domain goes, the words around it stay; only a run of spaces is
    # collapsed, so a leading one survives exactly as it does for a bare URL.
    assert unlink("openstreetmap.org contributors") == " contributors"


def test_prose_with_dots_in_it_is_not_a_link() -> None:
    # The rule has to leave an altitude, a file name and a missing space after
    # a full stop alone — the reason bare domains were let through before.
    for kept in (
        "Monte Misma, 1.161 m.s.l.m., graded T3 (sac_scale=3).",
        "The climb is steep.It flattens after the hut.",
        "Read route-document.schema.json for the contract.",
        "Sentiero del Viandante, 45.8 km, ends at Colico.",
    ):
        assert unlink(kept) == kept
