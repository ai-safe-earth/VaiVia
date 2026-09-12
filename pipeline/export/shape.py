"""Classifying a mapped route's shape: circular or linear.

The catalogue's generated routes know their shape because the generator drew
them that way (loop, destination). A mapped OSM relation knows nothing of the
kind — until 2026-08-21 every one wore ``shape='named'``, which says only "it
has a name". A walker deciding between a ring and a one-way traverse deserves
better, so the shape is MEASURED here and travels in the route document like
every other fact a reader needs.

The rule, in order:

1. **The mapper's word wins.** ``roundtrip=yes`` is a human saying "this comes
   back"; ``roundtrip=no`` is a human saying it does not. Both beat geometry,
   because the tag survives what geometry cannot: a ring our coverage clips at
   the bbox edge measures open but is still a ring (Giro del Pizzo di Cusio:
   650 m gap on 3.2 km, tagged yes).
2. **Untagged, single-line: the endpoint gap decides**, as a share of length.
   Measured over the 621 single-line relations on 2026-08-21: every true ring
   closes at ratio <= 0.0005 (34 exactly closed, one at 5.1 m over 10.8 km)
   and everything else jumps to >= 0.14 (short stubs whose gap is comparable
   to their length). GAP_RATIO = 0.01 sits an order of magnitude clear of both
   sides. A pure absolute threshold would fail the degenerate scraps — a 10 m
   fragment with a 9.7 m gap is not a ring.
3. **Untagged, in pieces: linear.** Closure cannot be measured across gaps,
   and the conservative error is the safe one: calling a linear route a loop
   strands a walker at the far end; calling a clipped ring linear merely
   under-sells it.
"""

from __future__ import annotations

# Endpoint gap as a share of route length, at or below which an untagged
# single-line route is circular. Measured 2026-08-21 (see module docstring):
# rings <= 0.0005, everything else >= 0.14; 0.01 is the geometric middle.
GAP_RATIO = 0.01


def classify_osm_shape(
    gap_m: float | None,
    distance_m: float | None,
    roundtrip: str | None,
) -> str:
    """'circular' or 'linear' for a mapped route.

    ``gap_m`` is the distance between the merged line's endpoints, or None
    when the route is held in pieces and has no two endpoints to measure.
    """
    if roundtrip == "yes":
        return "circular"
    if roundtrip == "no":
        return "linear"
    if gap_m is None or not distance_m or distance_m <= 0:
        return "linear"
    return "circular" if gap_m / distance_m <= GAP_RATIO else "linear"
