"""Which bounding box a GDS projection should cover.

There is exactly one rule, and it exists because the wrong answer has been
shipped three times (`docs/fragilities.md` #16): **a projection bbox is the
QUERY's or the GRAPH's, never the app's configured one.** `settings.bbox` is one
Lecco-shaped rectangle that holds 31,514 of the graph's 84,137 intersections once
Bergamo is ingested, and a projection built from it silently analyses 37% of the
network while reporting as though it had seen all of it.

`api/routes/routing.py` takes the first branch: a per-request box derived from
the endpoints and the distance cap. Analysis scripts take the second, and that is
what this module is — an explicit `--bbox` when the operator wants one, the whole
ingested graph otherwise.

Parsing is split from fetching so the parsing half is a pure function with a unit
test, and the fetching half is the one line that needs a database.
"""

from __future__ import annotations

import math
from typing import Protocol

Bbox = tuple[float, float, float, float]


class _Runner(Protocol):
    """Just enough of Neo4jClient to fetch the extent, so tests can fake it."""

    async def run_named(self, name: str, /, **params: object) -> list[dict]: ...


def parse_bbox(text: str) -> Bbox:
    """'min_lat,min_lon,max_lat,max_lon' -> the tuple, or raise.

    Raises rather than falling back to a default: an operator who passed --bbox
    asked for a specific box, and quietly substituting another one is how a run
    ends up covering ground nobody chose.
    """
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError(
            "--bbox must be 'min_lat,min_lon,max_lat,max_lon', " f"got {text!r}"
        )
    try:
        min_lat, min_lon, max_lat, max_lon = (float(part) for part in parts)
    except ValueError as error:
        raise ValueError(f"--bbox must be four floats, got {text!r}") from error
    if not all(math.isfinite(part) for part in (min_lat, min_lon, max_lat, max_lon)):
        # float() accepts 'nan' and 'inf', and every comparison below is False
        # for NaN, so an unchecked typo would project an empty graph and the
        # script would blame ingestion or GDS for the operator's own argument.
        raise ValueError(f"--bbox must be four finite floats, got {text!r}")
    if min_lat >= max_lat or min_lon >= max_lon:
        raise ValueError(
            "--bbox must read min_lat,min_lon,max_lat,max_lon with min < max, "
            f"got {text!r}"
        )
    return min_lat, min_lon, max_lat, max_lon


async def projection_bbox(db: _Runner, explicit: str | None = None) -> Bbox:
    """The box to project: `explicit` if given, else the whole ingested graph."""
    if explicit:
        return parse_bbox(explicit)
    rows = await db.run_named("graph_extent")
    if not rows or rows[0]["min_lat"] is None:
        raise RuntimeError(
            "the graph has no located intersections — is a region ingested?"
        )
    row = rows[0]
    return row["min_lat"], row["min_lon"], row["max_lat"], row["max_lon"]


def describe(bbox: Bbox, explicit: str | None = None) -> str:
    """One line naming the box and where it came from, for a script to print."""
    min_lat, min_lon, max_lat, max_lon = bbox
    source = "--bbox" if explicit else "the whole ingested graph"
    return (
        f"projecting {min_lat:.4f},{min_lon:.4f} to "
        f"{max_lat:.4f},{max_lon:.4f} ({source})"
    )


#: argparse help, so the three scripts describe the option identically.
BBOX_HELP = (
    "'min_lat,min_lon,max_lat,max_lon' to project over; "
    "default is the whole ingested graph"
)
