"""GraphHopper as a route source: geometry, elevation and per-activity profiles.

Decided in docs/routing-engine.md. It supplies what our own routing cannot:
round-trip loops that barely retrace (0.0-3.2% measured against ~20%), real
ascent from SRTM where every CONNECTS_TO edge reports 0 m (fragility #6), and
separate profiles per activity.

The activity split is the point, not a convenience. A foot loop with 200 steps
and a T4 scramble is not a bike route, it is impassable, so bike and foot
catalogues are GENERATED apart rather than one catalogue filtered after the
fact. infra/graphhopper/config.yml gives mtb its own profile with steps
excluded outright.

Returns the same RouteCandidate the local generator does, so scoring, dedup,
the POI map-back and persistence are unchanged — which is what putting
generation behind a seam was for.
"""

import logging
from typing import Any

import httpx

from core.geo import LatLon
from graph.route_scoring import RouteCandidate, retrace_fraction

logger = logging.getLogger(__name__)

# GraphHopper's road_class values that are not roads. Kept identical to the
# local generator's set so off-road shares from the two sources compare.
OFF_ROAD = {"path", "track", "bridleway", "footway", "steps", "cycleway"}

# Only these reach a user as an activity. The names match the profiles in
# infra/graphhopper/config.yml; a mismatch is a 400 from the engine, not a
# silent fallback to some default profile.
PROFILES = ("hike", "mtb")


class GraphHopperError(RuntimeError):
    pass


class GraphHopperClient:
    """Thin HTTP client. Holds no domain logic — that stays in the pipeline."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def info(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.get(f"{self._base_url}/info")
            response.raise_for_status()
            return response.json()

    async def available_profiles(self) -> list[str]:
        return [p["name"] for p in (await self.info()).get("profiles", [])]

    async def round_trip(
        self,
        start: LatLon,
        target_m: float,
        profile: str,
        seed: int,
        trailhead_id: str,
    ) -> RouteCandidate | None:
        """One circular route, or None when the engine cannot build it.

        None rather than raising: a batch generating thousands of candidates
        must treat an unroutable seed as a missing candidate, not a failure.
        """
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}; expected one of {PROFILES}")

        params = {
            "point": f"{start[0]},{start[1]}",
            "profile": profile,
            "algorithm": "round_trip",
            "round_trip.distance": int(target_m),
            "round_trip.seed": seed,
            "points_encoded": "false",
            "elevation": "true",
            "instructions": "false",
            "details": ["road_class", "hike_rating", "mtb_rating", "surface"],
            # Contraction hierarchies are off for these profiles, but say so
            # explicitly: with CH on, round_trip is silently rejected.
            "ch.disable": "true",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(f"{self._base_url}/route", params=params)
        except httpx.HTTPError as error:
            logger.warning("graphhopper unreachable: %s", str(error)[:160])
            return None

        if response.status_code != 200:
            logger.debug(
                "round_trip %s %dm seed=%d -> %d",
                profile,
                target_m,
                seed,
                response.status_code,
            )
            return None

        paths = (response.json() or {}).get("paths") or []
        if not paths:
            return None
        return _to_candidate(paths[0], trailhead_id, target_m, profile)


def _interval_lengths(
    details: list[list[Any]], coordinates: list[list[float]]
) -> dict[Any, float]:
    """Metres per detail value, measured from the geometry.

    Path details are [from_index, to_index, value] over the point list, so a
    value's share has to be measured along the line — counting intervals would
    weight a 20 m alley the same as a 2 km climb.
    """
    from core.geo import haversine_m

    out: dict[Any, float] = {}
    for start_i, end_i, value in details:
        metres = sum(
            haversine_m(
                (coordinates[i][1], coordinates[i][0]),
                (coordinates[i + 1][1], coordinates[i + 1][0]),
            )
            for i in range(start_i, min(end_i, len(coordinates) - 1))
        )
        out[value] = out.get(value, 0.0) + metres
    return out


def _to_candidate(
    path: dict[str, Any], trailhead_id: str, target_m: float, profile: str
) -> RouteCandidate | None:
    # GraphHopper emits [lon, lat, ele]; the rest of the codebase is (lat, lon).
    raw = path.get("points", {}).get("coordinates") or []
    if len(raw) < 2:
        return None
    coordinates: list[LatLon] = [(c[1], c[0]) for c in raw]

    distance_m = float(path.get("distance") or 0.0)
    if distance_m <= 0:
        return None

    details = path.get("details", {})
    by_class = _interval_lengths(details.get("road_class", []), raw)
    off_road_m = sum(m for k, m in by_class.items() if k in OFF_ROAD)

    # `ascend`, not `ascent`. The wrong key returns None silently and every
    # route then scores as "elevation unknown".
    ascend = path.get("ascend")

    candidate = RouteCandidate(
        trailhead_id=trailhead_id,
        target_m=target_m,
        coordinates=coordinates,
        distance_m=distance_m,
        off_road_share=off_road_m / distance_m if distance_m else 0.0,
        retrace=retrace_fraction(coordinates),
        ascent_m=float(ascend) if ascend is not None else None,
        source=f"graphhopper:{profile}",
    )
    # Difficulty as OSM records it, carried through so the catalogue can be
    # filtered on it: hike_rating is sac_scale, mtb_rating is mtb:scale.
    candidate.ratings = {
        "hike_rating": _weighted_max(details.get("hike_rating", []), raw),
        "mtb_rating": _weighted_max(details.get("mtb_rating", []), raw),
    }
    return candidate


def _weighted_max(
    details: list[list[Any]], coordinates: list[list[float]]
) -> int | None:
    """The hardest rating that covers a non-trivial share of the route.

    A plain max would let 30 m of scramble label a whole 20 km valley walk as
    alpine. Anything under 5% of the distance is treated as an incident rather
    than the character of the route.
    """
    if not details:
        return None
    lengths = _interval_lengths(details, coordinates)
    total = sum(lengths.values())
    if total <= 0:
        return None
    significant = [
        value
        for value, metres in lengths.items()
        if isinstance(value, int) and metres / total >= 0.05
    ]
    return max(significant) if significant else None
