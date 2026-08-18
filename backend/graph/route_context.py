"""Map a route polyline back onto the knowledge graph.

A routing engine returns geometry and little else. GraphHopper does not even
expose `osm_way_id` in its path details (docs/routing-engine.md), so a route
arrives as a bare list of coordinates with no link to anything we know about.
This module supplies the link, and it is the join the whole hybrid architecture
rests on: the engine says *where the line goes*, the graph says *what that
means*.

The join is **spatial rather than by id**, which is a feature and not a
workaround. An id join answers "which exact ways did this traverse"; a spatial
join answers "what does this route pass", which is the question a walker is
actually asking and the one the answer prose needs. It also survives the engine
re-splitting ways differently from our ingestion — the failure that orphaned
5,489 edges in fragility #11.

Two steps, because a point index cannot measure distance to a line:

  BOUND   sample the polyline, ask Cypher for anything near those samples.
          Index-backed and deliberately generous.
  EXACT   filter the candidates by true perpendicular distance to the line,
          in Python, with core.geo.distance_to_polyline_m.

Ingestion's PASSES_BY does the same two-step for a single segment; this
generalises it to a whole route.
"""

from typing import Any

from core.geo import LatLon, distance_to_polyline_m, haversine_m

# How far apart the bounding samples are. Any POI within `radius_m` of the line
# must be within `radius_m + SAMPLE_SPACING_M/2` of some sample, so the Cypher
# radius is padded by half the spacing and nothing is missed between samples.
SAMPLE_SPACING_M = 100.0
DEFAULT_RADIUS_M = 150.0


def sample_polyline(polyline: list[LatLon], spacing_m: float) -> list[LatLon]:
    """Vertices plus interpolated points, so no gap exceeds `spacing_m`.

    Vertices alone are not enough: a single straight edge kilometres long has
    only two, and everything beside its middle would be invisible to a
    radius-based query.
    """
    if len(polyline) < 2:
        return list(polyline)

    out: list[LatLon] = [polyline[0]]
    for start, end in zip(polyline, polyline[1:], strict=False):
        segment_m = haversine_m(start, end)
        if segment_m > spacing_m:
            steps = int(segment_m // spacing_m)
            for step in range(1, steps + 1):
                fraction = (step * spacing_m) / segment_m
                if fraction >= 1.0:
                    break
                out.append(
                    (
                        start[0] + (end[0] - start[0]) * fraction,
                        start[1] + (end[1] - start[1]) * fraction,
                    )
                )
        out.append(end)
    return out


async def pois_along_route(
    db: Any,
    polyline: list[LatLon],
    radius_m: float = DEFAULT_RADIUS_M,
    poi_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """POIs the route passes, nearest first, each with its distance to the line.

    `polyline` is (lat, lon) pairs. Engines commonly emit (lon, lat) — convert
    at the boundary, not here, so the mistake is made in one visible place.
    """
    if len(polyline) < 2:
        return []

    samples = sample_polyline(polyline, SAMPLE_SPACING_M)
    candidates = await db.run_named(
        "pois_near_points",
        points=[[lat, lon] for lat, lon in samples],
        # Padded so a POI sitting between two samples is still a candidate.
        radius_m=radius_m + SAMPLE_SPACING_M / 2,
    )

    found: list[dict[str, Any]] = []
    for poi in candidates:
        if poi_types and poi["type"] not in poi_types:
            continue
        distance_m = poi_distance_to_route(poi, polyline)
        if distance_m > radius_m:
            continue  # a bounding artefact, not actually near the line
        found.append({**poi, "distance_m": round(distance_m, 1)})

    found.sort(key=lambda p: p["distance_m"])
    return found


def poi_distance_to_route(poi: dict[str, Any], polyline: list[LatLon]) -> float:
    """How close the route comes to a POI, measuring to its SHAPE.

    For a node — a summit, a chapel — the centre is the thing, so distance to
    the point is right. For an area it is not: a lake's centroid sits out on
    the water, and Lago di Como's is 5.1 km from the nearest path, so measuring
    to it declares that no route in the region goes anywhere near the lake.
    What a walker means by "around the lake" is the shore, so an area is
    measured to its boundary.

    Boundaries are sampled to ~100 points at ingestion, so this stays a few
    thousand segment comparisons rather than millions.
    """
    boundary = poi.get("boundary")
    if not boundary:
        return distance_to_polyline_m((poi["lat"], poi["lon"]), polyline)
    return min(distance_to_polyline_m((lat, lon), polyline) for lat, lon in boundary)


def summarize_pois(pois: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape the map-back for an answer: what is passed, and what can be said.

    `described` carries only POIs with real prose. Wikidata one-liners are
    excluded deliberately — at ~27 characters they are category labels
    ("montagna delle Prealpi Lombarde") and presenting them as descriptions
    makes a route look richer than it is (docs/route-pipeline.md).
    """
    by_type: dict[str, int] = {}
    for poi in pois:
        by_type[poi["type"]] = by_type.get(poi["type"], 0) + 1

    named = [p for p in pois if p.get("name")]
    described = [p for p in pois if p.get("description_source") == "wikipedia"]
    # Attribution travels with the text, so a CC-BY-SA sentence can always be
    # credited wherever the answer surfaces it.
    attributions = sorted(
        {
            (p["description_url"], p["description_license"])
            for p in described
            if p.get("description_url")
        }
    )
    return {
        "count": len(pois),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "named": [{"name": p["name"], "type": p["type"]} for p in named],
        "described": [
            {
                "name": p.get("name"),
                "type": p["type"],
                "description": p["description"],
                "url": p.get("description_url"),
                "license": p.get("description_license"),
            }
            for p in described
        ],
        "attributions": [{"url": u, "license": lic} for u, lic in attributions],
    }
