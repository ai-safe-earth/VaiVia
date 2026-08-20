"""Enrichment: any provider's line, dressed with our network's metadata.

This is the step the whole spike exists to measure. No provider hands over
per-metre surface, SAC grade or bike legality for our ground — so whatever a
route's geometry came from, difficulty and the MTB verdict have to come from
matching that geometry onto the curated network and reading the edges it runs
along. If that works equally well for every provider, then geometry is the
interchangeable part and OUR STORE is the metadata backbone — which is the
"wisest combination" answer stated as a mechanism rather than an opinion.

Matching is deliberately dumb and measurable: an edge is ON the candidate line
when at least MATCH_SHARE of the edge lies within MATCH_M metres of it. No
graph walk, no map-matching HMM — a spike earns those only if this fails, and
`matched_share` in every result says whether it did.

The pure combination rules are NOT reimplemented here: difficulty is
export/document.py's ≥5% rule and surface its distribution, because the point
is to produce the same route document from every source. The one new rule is
the MTB verdict, and it is the access conjunction from metadata-rules.md:
one forbidding piece forbids the route.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from export.document import SAC_ORDER, Span, dominant, shares, significant_grade
from spike_providers.common import Candidate

# 25 m: generous enough to absorb provider geometry simplification (ORS
# simplifies its polylines), tight enough not to grab the parallel road across
# a valley. matched_share in the output is the check on this choice.
MATCH_M = 25.0
MATCH_SHARE = 0.5

# Edges within MATCH_M of the candidate line, with the share of each edge that
# actually lies inside the corridor — so a crossing edge (small share) can be
# told apart from a followed edge (large share).
MATCH = """
WITH line AS (
    SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(geojson)s), 4326), 32632) AS utm
),
-- Subdivided, and that is the whole trick. The corridor of a 101 km line is
-- one polygon with tens of thousands of vertices, and intersecting each
-- candidate edge with it cost minutes per route. Subdividing it into small
-- convex-ish pieces makes every intersection small-versus-small and lets the
-- planar edge index drive the join. Same family as the CTE-has-no-indexes trap
-- in metadata-rules.md.
corridor AS (
    SELECT ST_Subdivide(ST_Buffer(utm, %(match_m)s), 64) AS geom FROM line
)
SELECT e.edge_id,
       e.length_m,
       e.tags ->> 'surface',
       e.tags ->> 'sac_scale',
       e.tags ->> 'mtb:scale',
       e.routable_bike,
       e.routable_foot,
       sum(ST_Length(ST_Intersection(ST_Transform(e.geom, 32632), c.geom)))
           / NULLIF(ST_Length(ST_Transform(e.geom, 32632)), 0) AS inside_share
FROM corridor c
JOIN curated.edge e ON ST_Intersects(ST_Transform(e.geom, 32632), c.geom)
GROUP BY e.edge_id, e.length_m, e.tags, e.routable_bike, e.routable_foot
"""

LINE_LENGTH = """
SELECT ST_Length(ST_SetSRID(ST_GeomFromGeoJSON(%(geojson)s), 4326)::geography)
"""

# Places near the candidate line, the same 100 m bound and the same
# along-the-line positioning the route documents use — the route↔POI
# relationship, provider-independent.
PLACES = """
WITH line AS (
    SELECT ST_SetSRID(ST_GeomFromGeoJSON(%(geojson)s), 4326) AS geom
),
single AS (
    SELECT CASE WHEN GeometryType(geom) = 'LINESTRING' THEN geom END AS geom FROM line
)
SELECT p.source_id, p.kind, p.name,
       ST_X(p.geom), ST_Y(p.geom),
       ST_Distance(p.geom::geography, l.geom::geography) AS offset_m,
       CASE WHEN s.geom IS NOT NULL
            THEN ST_LineLocatePoint(s.geom, p.geom) * ST_Length(s.geom::geography)
       END AS along_m,
       p.is_start
FROM line l, single s, curated.place p
WHERE ST_DWithin(ST_Transform(p.geom, 32632), ST_Transform(l.geom, 32632), 100)
ORDER BY along_m NULLS LAST, offset_m
"""


class MatchedEdge(NamedTuple):
    edge_id: int
    length_m: float
    surface: str | None
    sac_scale: str | None
    mtb_scale: str | None
    routable_bike: bool
    routable_foot: bool
    inside_share: float | None


class Enrichment(NamedTuple):
    """What our store could say about somebody else's line."""

    line_length_m: float
    matched_edges: int
    matched_length_m: float
    matched_share: float  # matched metres / candidate line metres
    surface: dict[str, float]
    surface_dominant: str | None
    sac_scale: str | None
    mtb: dict[str, Any]
    places: list[dict[str, Any]]


def followed(
    edges: list[MatchedEdge], min_share: float = MATCH_SHARE
) -> list[MatchedEdge]:
    """The edges the line actually runs along, not the ones it merely crosses."""
    return [e for e in edges if (e.inside_share or 0.0) >= min_share]


def mtb_verdict(edges: list[MatchedEdge]) -> dict[str, Any]:
    """Ridability of the matched ground, by the rules already ratified.

    Legality is a CONJUNCTION (metadata-rules.md): one forbidding piece forbids
    the route. Technical grade is the ≥5% rule over mtb:scale, same as SAC.
    Nothing matched means nothing is known — absent is not "yes".
    """
    if not edges:
        return {"rideable": None, "reason": "no matched ground", "mtb_scale": None}
    blocked = [e for e in edges if not e.routable_bike]
    if blocked:
        metres = sum(e.length_m for e in blocked)
        return {
            "rideable": False,
            "reason": f"{len(blocked)} matched edges ({metres:.0f} m) are not "
            "legally bikeable — one forbidding piece forbids the route",
            "mtb_scale": None,
        }
    grade = significant_grade(
        [Span(e.mtb_scale, e.length_m) for e in edges],
        ["0", "1", "2", "3", "4", "5", "6"],
    )
    return {"rideable": True, "reason": None, "mtb_scale": grade}


def combine(
    line_length_m: float, edges: list[MatchedEdge], places: list[dict]
) -> Enrichment:
    """Pure: matched edges + line length -> the enrichment. Pinned by tests."""
    on_route = followed(edges)
    matched_length = sum(e.length_m for e in on_route)
    surface = shares(Span(e.surface, e.length_m) for e in on_route)
    return Enrichment(
        line_length_m=line_length_m,
        matched_edges=len(on_route),
        matched_length_m=matched_length,
        matched_share=(
            min(matched_length / line_length_m, 1.0) if line_length_m > 0 else 0.0
        ),
        surface=surface,
        surface_dominant=dominant(surface),
        sac_scale=significant_grade(
            [Span(e.sac_scale, e.length_m) for e in on_route], SAC_ORDER
        ),
        mtb=mtb_verdict(on_route),
        places=places,
    )


def line_geojson(candidate: Candidate) -> str:
    if len(candidate.paths) == 1:
        return json.dumps(
            {
                "type": "LineString",
                "coordinates": [[x, y] for x, y in candidate.paths[0]],
            }
        )
    return json.dumps(
        {
            "type": "MultiLineString",
            "coordinates": [[[x, y] for x, y in p] for p in candidate.paths],
        }
    )


def enrich(conn, candidate: Candidate) -> Enrichment:
    """Fetch the matched edges and places for one candidate, then combine."""
    geojson = line_geojson(candidate)
    (length_m,) = conn.execute(LINE_LENGTH, {"geojson": geojson}).fetchone()
    edges = [
        MatchedEdge(*row)
        for row in conn.execute(MATCH, {"geojson": geojson, "match_m": MATCH_M})
    ]
    places = [
        {
            "id": source_id,
            "kind": kind,
            "name": name,
            "lon": round(lon, 6),
            "lat": round(lat, 6),
            "offset_m": round(offset_m, 1),
            "distance_along_m": None if along_m is None else round(along_m, 1),
            "is_start": is_start,
        }
        for source_id, kind, name, lon, lat, offset_m, along_m, is_start in conn.execute(
            PLACES, {"geojson": geojson}
        )
    ]
    return combine(float(length_m or 0.0), edges, places)
