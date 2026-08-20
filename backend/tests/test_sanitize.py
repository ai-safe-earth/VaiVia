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
