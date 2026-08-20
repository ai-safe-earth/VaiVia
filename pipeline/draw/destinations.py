"""Choosing where an out-and-back goes. Pure, tested.

A destination route exists because its endpoint is worth standing at — a
summit, a view, a waterfall, a rifugio. The ranking below says which of the
places near a start earn a route, and it is v0 WEIGHTS AS PARAMETERS, the same
posture as the scorer: a recalibration is an argument, not a code change.

Named beats unnamed, deliberately and heavily: "to Rifugio Elisa" is an answer
a person can act on, and 219 of the 240 reachable peaks carry a name — the
unnamed remainder can wait for the catalogue that has nothing better left.

The crow-flies band is generous by design. Measured wander (walked / crow) on
real starts ranges 1.4-3.3 in these mountains — a hut across a ridge walks
three times its crow distance — so no band can promise length. The band only
keeps the candidate pool sane; the actual routed distance lands in the score's
length-fit term, which is where a wrong-length route loses.
"""

from __future__ import annotations

from typing import NamedTuple

# Why these numbers: what a walker crosses a valley FOR, roughly ordered.
# Peaks and views are the archetype (the owner's own examples); huts add
# refreshment and a turnaround that feels like one; water features draw
# families; saddles are earned but underwhelming to stand at; springs and
# picnic sites are waypoints, not destinations.
INTEREST: dict[str, float] = {
    "peak": 5.0,
    "viewpoint": 5.0,
    "hut": 4.5,
    "waterfall": 4.0,
    "lake": 4.0,
    "castle": 4.0,
    "cave": 3.0,
    "ruins": 3.0,
    "beach": 3.0,
    "chapel": 2.0,
    "saddle": 2.0,
    "spring": 1.0,
}

NAMED_BONUS = 3.0

# The out leg is roughly target/2 of walking. Wander (walked/crow) measured
# 1.4-3.3, so the crow band spans that range rather than betting on a mean.
BAND_LOW_DIVISOR = 3.5  # crow >= (target/2) / 3.5 — not so close it is a stroll
BAND_HIGH_DIVISOR = 1.1  # crow <= (target/2) / 1.1 — reachable at all


class Destination(NamedTuple):
    place_id: str
    kind: str
    name: str | None
    vertex_id: int
    crow_m: float


def crow_band(target_m: float) -> tuple[float, float]:
    """The crow-flies distance band a destination should sit in."""
    half = target_m / 2.0
    return half / BAND_LOW_DIVISOR, half / BAND_HIGH_DIVISOR


def interest(destination: Destination) -> float:
    base = INTEREST.get(destination.kind, 0.0)
    if destination.name:
        base += NAMED_BONUS
    return base


def rank(candidates: list[Destination], *, top: int) -> list[Destination]:
    """The destinations worth routing to, best first.

    Ties break on crow distance (nearer first) then id, so the same pool always
    ranks the same way — determinism is what keeps route ids stable.
    """
    return sorted(
        candidates,
        key=lambda d: (-interest(d), d.crow_m, d.place_id),
    )[:top]


def route_name(destination: Destination) -> str | None:
    """What the route is called: the destination, or nothing.

    An unnamed destination gives no name — "to unnamed viewpoint" is worse
    than silence, and selection already prefers named destinations heavily.
    """
    if not destination.name:
        return None
    return f"To {destination.name}"
