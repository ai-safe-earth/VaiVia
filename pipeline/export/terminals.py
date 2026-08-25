"""Terminals: where a walker joins or leaves a route from the outside world.

The start/end contract (docs/route-document.md, ratified 2026-08-25) makes
reachability a property of a TERMINAL — one per loop / circular /
out-and-back / destination route, two for a linear traverse, each tested
independently. The test is NETWORK distance over foot-legal edges to the
nearest start vertex, never straight line (which crosses rivers, cliffs and
private land), bounded at the measured 1 km knee of the endpoint-to-start
histogram.

Seasons: the default INVERTS the hazard rule. An unscoped way in is
reachable in every season — absence of a gate tag is evidence there is no
gate. Only a terminal whose ONLY in-reach starts are transit stops takes
its seasons from the feed's measured service span, and those are marked
`unverified`: a GTFS feed's end date is a publication horizon, not a
closure, so "the feed stops in December" must never silently become "no
winter service" — nor "year-round" either.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

#: The measured knee: endpoint-to-start counts decay 533 -> 26 over the first
#: kilometre and go flat at background density through the second
#: (docs/route-document.md §3). Network distance, so a ceiling, never floor.
REACH_LIMIT_M = 1000.0

#: How many in-reach start names a terminal carries — nearest first; more is
#: an inventory, not an arrival.
NAME_CAP = 3

NEAREST_VERTEX = """
SELECT vertex_id, ST_AsGeoJSON(geom)
FROM source_map.vertex
ORDER BY ST_Transform(geom, 32632)
     <-> ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 32632)
LIMIT 1
"""

#: Every start vertex within network reach of the terminal, with the feed
#: span where the start is a transit stop. pgr_drivingDistance includes the
#: root at cost 0, so a terminal that IS a start reads nearest_start_m 0.
STARTS_WITHIN_REACH = """
WITH reach AS (
    SELECT node, agg_cost
    FROM pgr_drivingDistance(
        'SELECT edge_id AS id, source, target, length_m AS cost,
                length_m AS reverse_cost
         FROM source_map.edge WHERE routable_foot',
        %(vertex)s::bigint, %(limit_m)s::float8, false)
)
SELECT p.start_class,
       p.name,
       (p.source = 'gtfs_stop' OR p.kind = 'station') AS car_free,
       r.agg_cost,
       g.service_start,
       g.service_end
FROM reach r
JOIN source_map.place p ON p.vertex_id = r.node AND p.is_start
LEFT JOIN staging.gtfs_stop g
       ON p.source = 'gtfs_stop'
      AND p.source_id = g.feed || ':' || g.stop_id
ORDER BY r.agg_cost, p.name NULLS LAST, p.source_id
"""

#: Which months make each season. Winter wraps the year end.
SEASON_MONTHS = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "autumn": (9, 10, 11),
    "winter": (12, 1, 2),
}

#: A start whose way in is not timetabled: reachable whenever the ground is.
TIMELESS_CLASSES = frozenset(
    {"parking", "settlement", "urban_exit", "campsite", "other", "station"}
)


def months_of(start: date, end: date) -> set[int]:
    """The calendar months a service span touches. A span of a year or more
    touches all twelve however it sits."""
    if (end - start).days >= 365:
        return set(range(1, 13))
    months: set[int] = set()
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.add(month)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def seasons_block(rows: list[tuple]) -> dict[str, bool]:
    """The seasons a terminal is reachable in, from its in-reach starts.

    rows: (start_class, name, car_free, agg_cost, service_start, service_end).

    Any timeless start makes every season True with nothing to verify. A
    transit-only terminal takes the union of its feeds' month coverage, and
    is `unverified`: the feed proves service only inside its published
    window, and says nothing either way beyond it.
    """
    if not rows:
        return {
            "spring": False,
            "summer": False,
            "autumn": False,
            "winter": False,
            "unverified": False,
        }
    if any(row[0] in TIMELESS_CLASSES or row[0] is None for row in rows):
        return {
            "spring": True,
            "summer": True,
            "autumn": True,
            "winter": True,
            "unverified": False,
        }
    months: set[int] = set()
    dated = False
    for _cls, _name, _car_free, _cost, service_start, service_end in rows:
        if service_start and service_end:
            dated = True
            months |= months_of(service_start, service_end)
    seasons = {
        season: bool(months & set(SEASON_MONTHS[season])) for season in SEASON_MONTHS
    }
    # Transit-only is ALWAYS unverified: the feed proves service only inside
    # its published window (and an uncalendared stop proves nothing at all —
    # `dated` exists so a reader of this code sees the case was considered,
    # not because it changes the verdict).
    del dated
    seasons["unverified"] = True
    return seasons


def terminal_for_vertex(conn, vertex_id: int, point_geojson: str) -> dict[str, Any]:
    rows = conn.execute(
        STARTS_WITHIN_REACH, {"vertex": vertex_id, "limit_m": REACH_LIMIT_M}
    ).fetchall()
    names: list[str] = []
    for _cls, name, _car_free, _cost, _s, _e in rows:
        if name and name not in names:
            names.append(name)
        if len(names) == NAME_CAP:
            break
    return {
        "vertex_id": vertex_id,
        "point": json.loads(point_geojson),
        "names": names,
        "start_classes": sorted({row[0] for row in rows if row[0]}),
        "car_free": any(row[2] for row in rows),
        "nearest_start_m": round(float(rows[0][3]), 1) if rows else None,
        "reachable": bool(rows),
        "seasons": seasons_block(rows),
    }


def terminal_for_point(conn, lon: float, lat: float) -> dict[str, Any]:
    """The terminal at a geometry endpoint. Endpoints of merged route lines
    coincide with network vertices, so the nearest vertex IS the endpoint."""
    vertex_id, point = conn.execute(NEAREST_VERTEX, {"lon": lon, "lat": lat}).fetchone()
    return terminal_for_vertex(conn, vertex_id, point)


def endpoints_of(geometry: dict[str, Any], shape: str) -> list[tuple[float, float]]:
    """The terminal endpoints of a route geometry, per shape.

    One for the shapes a walker leaves from where they arrived (loop,
    circular, out_and_back, destination); the two OUTERMOST endpoints of the
    merged geometry for a linear traverse — a route in nine pieces has two
    terminals, not eighteen (start/end contract §7).
    """
    if geometry["type"] == "LineString":
        coords = geometry["coordinates"]
        first, last = coords[0], coords[-1]
    else:
        pieces = geometry["coordinates"]
        first, last = pieces[0][0], pieces[-1][-1]
    if shape == "linear":
        return [(first[0], first[1]), (last[0], last[1])]
    return [(first[0], first[1])]
