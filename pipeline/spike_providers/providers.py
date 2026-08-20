"""The four providers, each normalised to Candidate routes.

Two shapes exist in the wild, and the methodology forks on them exactly as the
brief says:

  2a ROUTES  — the provider hands over finished route lines. OSM relations and
               TrailSplits (which serves OSM relations) are this. Nothing to
               draw; go straight to enrichment.
  2b ENGINE  — the provider hands over a ROUTER over segments, not routes. ORS
               and FreeRoute are this: the segment join ("wise data management
               for joins between segments") is their engine's job, and what we
               receive is already a drawn line. Our own OSM path through
               build_network is the self-hosted version of the same fork.

What none of them hands over is per-metre metadata for OUR ground — which is
the point the spike exists to make measurable. Enrichment is a separate step
(enrich.py) applied identically to every candidate.
"""

from __future__ import annotations

import json

from core import env_value
from spike_providers.common import LECCO, Candidate, fetch, geojson_paths

# ── OSM (baseline): the relations already joined onto the network ────────────

# The comparison baseline reads the SAME store the enrichment reads, through
# the same merged line the route documents use. Circular for scoring geometry
# match (it will be ~100% by construction, and the findings say so); its value
# in the comparison is metadata coverage, which is not circular.
OSM_LINES = """
SELECT r.rel_id,
       r.tags ->> 'name',
       r.tags ->> 'ref',
       r.tags ->> 'route',
       ST_AsGeoJSON(ST_Multi(ST_LineMerge(ST_Collect(e.geom))))
FROM staging.osm_relation r
JOIN (SELECT DISTINCT rel_id, edge_id FROM curated.edge_route) er ON er.rel_id = r.rel_id
JOIN curated.edge e ON e.edge_id = er.edge_id
WHERE r.rel_id = ANY(%(ids)s)
GROUP BY r.rel_id, r.tags
"""


def osm_candidates(conn, rel_ids: list[int]) -> list[Candidate]:
    out = []
    for rel_id, name, ref, activity, geometry in conn.execute(
        OSM_LINES, {"ids": rel_ids}
    ):
        out.append(
            Candidate(
                provider="osm",
                candidate_id=f"osm-relation-{rel_id}",
                name=name or (f"sentiero {ref}" if ref else None),
                paths=geojson_paths(json.loads(geometry)),
                activity=activity,
                extra={"ref": ref},
            )
        )
    return out


# ── TrailSplits: OSM route relations, served as an API ───────────────────────

TRAILSPLITS = "https://api.trailsplits.com"


def trailsplits_list(limit: int = 25) -> tuple[list[dict], str | None]:
    """The trails whose bbox intersects Lecco. Returns (features, error)."""
    min_lat, min_lon, max_lat, max_lon = LECCO
    status, body = fetch(
        f"{TRAILSPLITS}/trails/v1/bbox",
        params={
            "min_lat": min_lat,
            "min_lng": min_lon,
            "max_lat": max_lat,
            "max_lng": max_lon,
            "type": "hiking",
            "limit": limit,
        },
    )
    if status != 200:
        return [], f"trails/v1/bbox returned {status}"
    return json.loads(body).get("features", []), None


def trailsplits_candidates(limit: int = 6) -> tuple[list[Candidate], list[str]]:
    """Fetch full lines for relations that mostly lie inside our coverage.

    The bbox listing matches anything whose BOX touches Lecco — the Chemin
    d'Assise (Vézelay to Assisi) arrives because its box spans half the Alps.
    Selecting on the box being CONTAINED in a padded Lecco box keeps the
    comparison to routes our store can actually be compared against.
    """
    notes: list[str] = []
    features, error = trailsplits_list()
    if error:
        return [], [error]

    min_lat, min_lon, max_lat, max_lon = LECCO
    pad = 0.15
    local = [
        f
        for f in features
        if (b := f["properties"].get("bbox"))
        and b[0] >= min_lon - pad
        and b[1] >= min_lat - pad
        and b[2] <= max_lon + pad
        and b[3] <= max_lat + pad
    ]
    notes.append(
        f"bbox listing returned {len(features)} trails, {len(local)} local to Lecco"
    )

    out: list[Candidate] = []
    for feature in local[:limit]:
        rel_id = feature["properties"]["osm_relation_id"]
        status, body = fetch(f"{TRAILSPLITS}/trails/v1/relation/{rel_id}")
        if status != 200:
            notes.append(f"relation {rel_id}: HTTP {status}")
            continue
        detail = json.loads(body)
        props = detail["properties"]
        out.append(
            Candidate(
                provider="trailsplits",
                candidate_id=f"trailsplits-{rel_id}",
                name=props.get("name") or props.get("ref"),
                paths=geojson_paths(detail["geometry"]),
                activity=props.get("route_type"),
                extra={
                    "osm_relation_id": rel_id,
                    "ref": props.get("ref"),
                    "tier": props.get("tier"),
                    "provider_distance_m": props.get("distance_m"),
                },
            )
        )
    return out, notes


def trailsplits_pois(limit: int = 50) -> tuple[list[dict], str | None]:
    """Their POI layer over Lecco, for the coverage comparison against ours."""
    min_lat, min_lon, max_lat, max_lon = LECCO
    status, body = fetch(
        f"{TRAILSPLITS}/pois/v1/bbox",
        params={
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon,
            "kind": "hut,peak,water,viewpoint",
            "limit": limit,
        },
    )
    if status != 200:
        return [], f"pois/v1/bbox returned {status}"
    return json.loads(body).get("features", []), None


# ── ORS-compatible engines: OpenRouteService, and FreeRoute ──────────────────

# One client, two providers: FreeRoute exposes the ORS path convention
# (/v1/directions/{profile}), so the same code exercises both and the
# comparison between them is exactly the base URL and the key.

ORS = "https://api.openrouteservice.org/v2"
FREEROUTE = "https://api.maps.freeroute.org/v1"


def ors_compatible_route(
    provider: str,
    base_url: str,
    profile: str,
    coordinates: list[tuple[float, float]],
    *,
    api_key: str | None,
    round_trip_m: float | None = None,
    geojson_endpoint: bool = True,
) -> tuple[Candidate | None, str | None]:
    """One directions request against an ORS-shaped API. (candidate, error)."""
    body: dict = {"coordinates": [[x, y] for x, y in coordinates]}
    if round_trip_m:
        # ORS draws a loop from a single anchor: the engine doing our 2b step.
        body["options"] = {"round_trip": {"length": round_trip_m, "points": 4}}
    headers = {"Authorization": api_key} if api_key else {}
    # ORS serves GeoJSON at .../geojson; FreeRoute 404s that suffix and only
    # reaches a handler on the bare path (which then 500s — measured, and the
    # measurement is the finding). Ask for the shape each host actually has.
    suffix = "/geojson" if geojson_endpoint else ""
    status, text = fetch(
        f"{base_url}/directions/{profile}{suffix}",
        method="POST",
        json_body=body,
        headers=headers,
    )
    if status != 200:
        return None, f"{provider} {profile}: HTTP {status} — {text[:140]}"
    payload = json.loads(text)
    features = payload.get("features") or []
    if not features:
        return None, f"{provider} {profile}: no route in response"
    feature = features[0]
    summary = feature.get("properties", {}).get("summary", {})
    kind = "loop" if round_trip_m else "p2p"
    return (
        Candidate(
            provider=provider,
            candidate_id=f"{provider}-{profile}-{kind}",
            name=f"{provider} {profile} {kind}",
            paths=geojson_paths(feature["geometry"]),
            activity="mtb" if "cycling" in profile else "hiking",
            extra={"profile": profile, "provider_distance_m": summary.get("distance")},
        ),
        None,
    )


def ors_candidates(anchor: tuple[float, float], across: tuple[float, float]):
    """ORS proper. Needs ORS_API_KEY (free tier) in the env or repo .env."""
    key = env_value("ORS_API_KEY")
    if not key:
        return [], [
            (
                "ORS_API_KEY is not set — client is ready, run skipped. "
                "Free key: https://openrouteservice.org/dev/"
            )
        ]
    out, notes = [], []
    for profile, coords, loop in (
        ("foot-hiking", [anchor, across], None),
        ("cycling-mountain", [anchor, across], None),
        ("foot-hiking", [anchor], 8000.0),
    ):
        candidate, error = ors_compatible_route(
            "ors", ORS, profile, coords, api_key=key, round_trip_m=loop
        )
        if candidate:
            out.append(candidate)
        if error:
            notes.append(error)
    return out, notes


def freeroute_candidates(anchor: tuple[float, float], across: tuple[float, float]):
    """FreeRoute: same client, their base URL, no key documented.

    Profile names are THEIR list, returned by their own 400 ("Valid profiles:
    driving-car, foot-walking, cycling-regular, cycling-road, cycling-mountain,
    ..."). With valid profiles every request 500s — driving-car in central
    Milan included — so the façade is up and the engine behind it is down.
    """
    out, notes = [], []
    for profile in ("foot-walking", "cycling-mountain", "driving-car"):
        candidate, error = ors_compatible_route(
            "freeroute",
            FREEROUTE,
            profile,
            [anchor, across],
            api_key=None,
            geojson_endpoint=False,
        )
        if candidate:
            out.append(candidate)
        if error:
            notes.append(error)
    return out, notes
