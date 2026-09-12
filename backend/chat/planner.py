"""Draw an outing at ask time, over the loaded pack.

The R4 pipeline (docs/route-design.md, "Ask time"): compiled constraints →
candidate starts → draw per start → facts → reject by STATED limits,
counting each rejection → one rung of relaxation → keep_distinct → 3.
Documents are built in-process from the same rules the catalogue used and
ids are minted last; nothing is written anywhere until favourite/share.

Everything here is synchronous CPU over in-memory arrays (call it via
asyncio.to_thread); name resolution — the only I/O an ask needs — happens
in the orchestrator before this module is entered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from vaivia_routes.assemble import (
    MTB_ORDER,
    Assembled,
    assemble,
    assert_connected,
    score,
)
from vaivia_routes.destinations import Destination, crow_band, rank, route_name
from vaivia_routes.document import SAC_ORDER, Span, build_document, dominant, shares
from vaivia_routes.draw import draw_loop, draw_out_and_back, draw_strict_out_and_back
from vaivia_routes.ids import DIRECTED_SHAPES, forward_is_stored, route_id
from vaivia_routes.loops import keep_distinct
from vaivia_routes.network import Network
from vaivia_routes.pack import Pack

from chat.compile import Constraints
from chat.intents import ClarifyIntent
from chat.pack_state import PlannerState

#: How many candidate starts an ask fans out over, and loop seeds per start.
N_STARTS = 5
SEEDS = 4
KEEP = 3

#: When neither hours nor a distance cap was stated, the target the shapes
#: aim for — a normal half-day out, labelled in the assumptions strip.
DEFAULT_TARGET_M = {"foot": 10000.0, "mtb": 18000.0}

#: The one relaxation rung: the stated distance band widened by half, the
#: share caps (urban, surface) lifted. Hard safety caps (sac/mtb, ascent)
#: are never relaxed — they are promises, not preferences.
RELAX_BAND = 1.5

#: Destination kinds when the ask names none (the catalogue's INTEREST set
#: does the ranking; this only bounds the pool).
PLACES_M = 100.0

METRES_PER_DEG_LAT = 111_320.0

SOURCES = [
    {
        "name": "OpenStreetMap",
        "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
    }
]


@dataclass
class PlannerResult:
    """Three routes at most, and the counts that explain every absence."""

    routes: list[dict] = field(default_factory=list)  # {"card": ..., "document": ...}
    counts: dict[str, int] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    relaxed: str | None = None
    clarify: ClarifyIntent | None = None


def _metric(lat: float) -> tuple[float, float]:
    return METRES_PER_DEG_LAT * math.cos(math.radians(lat)), METRES_PER_DEG_LAT


def plan_outing(
    state: PlannerState,
    constraints: Constraints,
    anchor: tuple[float, float] | None = None,
) -> PlannerResult:
    """The ask, answered: (lat, lon) `anchor` is the resolved start area —
    ChatRequest.near for "here", a geocoded name for the rest, or None."""
    out = PlannerResult(assumptions=list(constraints.assumptions))

    if constraints.days > 1:
        # Multi-day treks are NOT offered (owner, 2026-09-12) — the ask is
        # understood and refused honestly; a day loop sold as a trek would
        # be the kind of lie counts exist to prevent.
        out.clarify = ClarifyIntent(
            question=(
                "VaiVia plans single-day outings — multi-day treks are not "
                "offered. Want a full-day outing in the same area instead?"
            ),
            suggestions=["a challenging full-day loop in the Orobie"],
        )
        return out

    net = _network(state, constraints)

    band = constraints.distance_band_m
    target_m = (
        (band[0] + band[1]) / 2 if band else DEFAULT_TARGET_M[constraints.activity]
    )
    if band is None:
        out.assumptions.append(
            f"no length stated — aiming for ~{target_m / 1000:.0f} km (our estimate)"
        )

    shape = constraints.shape or (
        "out_and_back"
        if any(w.role in ("end", "bathe") for w in constraints.waypoints)
        else "loop"
    )
    wanted_kinds: set[str] = set()
    if shape != "loop":
        for w in constraints.waypoints:
            if w.role in ("end", "bathe", "eat"):
                wanted_kinds.update(w.kinds)

    starts = _candidate_starts(
        state.pack,
        net,
        constraints,
        anchor,
        out,
        wanted_kinds or None,
        target_m,
    )
    if not starts:
        out.clarify = ClarifyIntent(
            question=(
                "I could not find a place to start from for that ask — "
                "name a town or trailhead, widen the drive time, or share "
                "your location?"
            ),
            suggestions=["start near Lecco", "from Bergamo, by train"],
        )
        return out

    candidates: list[dict] = []
    for vertex, lon, lat in starts:
        if shape == "loop":
            for seed in range(SEEDS):
                walked = draw_loop(net, vertex, (lon, lat), target_m, seed)
                _collect(candidates, out.counts, walked, vertex, target_m, shape)
        else:
            pool = _destinations(state.pack, net, constraints, lon, lat, target_m)
            if not pool:
                out.counts["no destination of the wanted kind in reach"] = (
                    out.counts.get("no destination of the wanted kind in reach", 0) + 1
                )
            for destination in pool:
                draw = (
                    draw_strict_out_and_back
                    if shape == "out_and_back"
                    else draw_out_and_back
                )
                walked = draw(net, vertex, destination.vertex_id)
                _collect(
                    candidates,
                    out.counts,
                    walked,
                    vertex,
                    target_m,
                    shape,
                    destination,
                )

    kept = _reject_and_keep(candidates, constraints, out)
    for entry in kept:
        document = _build_document(state.pack, entry, constraints, shape)
        card = _card(entry, document, constraints)
        out.routes.append({"card": card, "document": document})

    if not out.routes:
        out.clarify = _infeasible_clarify(out.counts)
    return out


def _in_polygon(lon: np.ndarray, lat: np.ndarray, polygon: list) -> np.ndarray:
    """Ray-cast point-in-polygon over a lon/lat ring — the gazetteer's
    polygons are coarse by design, so a plain even-odd test is the whole
    algorithm."""
    ring = np.asarray(polygon, dtype=np.float64)
    inside = np.zeros(len(lon), dtype=bool)
    x1, y1 = ring[:-1, 0], ring[:-1, 1]
    x2, y2 = ring[1:, 0], ring[1:, 1]
    for a1, b1, a2, b2 in zip(x1, y1, x2, y2, strict=True):
        crosses = ((b1 > lat) != (b2 > lat)) & (
            lon < (a2 - a1) * (lat - b1) / (b2 - b1 + 1e-300) + a1
        )
        inside ^= crosses
    return inside


def _drive_minutes_from(
    pack: Pack, anchor: tuple[float, float]
) -> tuple[np.ndarray, str] | None:
    """Minutes to every start PLACE from the settlement nearest the anchor,
    with the settlement's name — or None when the pack has no drive rows."""
    rows, _cols, matrix = pack.drive_matrix()
    if len(rows) == 0:
        return None
    kx, ky = _metric(anchor[0])
    d2 = (kx * (pack["place_lon"][rows] - anchor[1])) ** 2 + (
        ky * (pack["place_lat"][rows] - anchor[0])
    ) ** 2
    nearest = int(d2.argmin())
    name = str(pack["place_name"][rows[nearest]]) or "the nearest town"
    return matrix[nearest].astype(np.float32), name


# ── starts ───────────────────────────────────────────────────────────────────


def _candidate_starts(
    pack: Pack,
    net: Network,
    constraints: Constraints,
    anchor: tuple[float, float] | None,
    result: PlannerResult | None = None,
    wanted_kinds: set[str] | None = None,
    target_m: float | None = None,
) -> list[tuple[int, float, float]]:
    """(vertex index, lon, lat) of the starts this ask fans out over.

    Selection, in order: the mode's class filter; the asked area's polygon;
    the drive matrix when a drive-time limit is stated (crow-fly at 50 km/h
    as the said-out-loud fallback when the pack carries no matrix); then
    ranking — near the anchor, else busiest station, else the starts that
    open the most unpaved ground (trail_share). For a destination shape,
    starts whose potential field puts a wanted kind inside the crow band
    sort first: "end at a lake" is a lookup, not a hope.
    """
    is_start = pack["place_is_start"]
    classes = np.array(pack.decode("place_start_class"), dtype=object)
    mask = is_start.copy()
    if constraints.start_mode in ("station", "parking"):
        mask &= classes == constraints.start_mode
    vertices = pack["place_vertex"]
    on_main = pack["vertex_component"][vertices] == net.main_component
    mask &= on_main

    if constraints.area_polygon:
        mask &= _in_polygon(
            pack["place_lon"], pack["place_lat"], constraints.area_polygon
        )

    if constraints.max_drive_min is not None and anchor is not None:
        looked_up = _drive_minutes_from(pack, anchor)
        if looked_up is not None:
            minutes, settlement = looked_up
            reachable = np.zeros(len(mask), dtype=bool)
            cols = pack["drive_col_place"]
            reachable[cols[minutes <= constraints.max_drive_min]] = True
            mask &= reachable
            if result is not None:
                result.assumptions.append(
                    f"within {constraints.max_drive_min:.0f} min drive of "
                    f"{settlement} (measured on the road network)"
                )
        else:
            radius_m = constraints.max_drive_min * (50.0 / 60.0) * 1000
            kx, ky = _metric(anchor[0])
            d2 = (kx * (pack["place_lon"] - anchor[1])) ** 2 + (
                ky * (pack["place_lat"] - anchor[0])
            ) ** 2
            mask &= d2 <= radius_m**2
            if result is not None:
                result.assumptions.append(
                    f"~{constraints.max_drive_min:.0f} min drive read as "
                    f"{radius_m / 1000:.0f} km as the crow flies (no road "
                    "matrix in this pack)"
                )

    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    lon = pack["place_lon"][idx]
    lat = pack["place_lat"][idx]
    if anchor is not None:
        kx, ky = _metric(anchor[0])
        rank_key = (kx * (lon - anchor[1])) ** 2 + (ky * (lat - anchor[0])) ** 2
    elif constraints.start_mode == "station":
        rank_key = -pack["place_n_trips"][idx].astype(np.float64)
    else:
        # No anchor: the starts that open the most unpaved ground first.
        share = pack["start_trail_share_5km"][idx]
        rank_key = -np.nan_to_num(share, nan=0.0).astype(np.float64)

    if wanted_kinds and target_m:
        # Feasible-first, not feasible-only: the potential field says which
        # starts can END at a wanted kind inside the crow band; the others
        # stay behind them rather than vanishing.
        low, high = crow_band(target_m)
        best = np.full(len(idx), np.inf)
        for kind in wanted_kinds:
            if kind in pack.manifest["codes"]["place_kind"]:
                field = pack.potential(kind).astype(np.float64)
                best = np.minimum(best, field[vertices[idx]])
        feasible = (best >= low * 0.5) & (best <= high * 1.5)
        order = np.lexsort((rank_key, ~feasible))
    else:
        order = np.argsort(rank_key)

    out: list[tuple[int, float, float]] = []
    seen: set[int] = set()
    for k in order:
        vertex = int(vertices[idx[k]])
        if vertex in seen:
            continue
        seen.add(vertex)
        out.append(
            (
                vertex,
                float(pack["vertex_lon"][vertex]),
                float(pack["vertex_lat"][vertex]),
            )
        )
        if len(out) >= N_STARTS:
            break
    return out


# ── the network, with surface exclusions priced in ───────────────────────────


def _network(state: PlannerState, constraints: Constraints) -> Network:
    """The activity's network; excluded surfaces cost ×factor.

    The factors multiply the pack's cost columns BEFORE the CSR is built, so
    the reduction invariants hold; a -1 arc stays -1 (a penalty never makes
    illegal merely expensive). ponytail: rebuilt per ask when exclusions are
    present (~140 ms); cache per exclusion set if that ever shows up.
    """
    base = state.networks[constraints.activity]
    if not constraints.surface_cost_factors:
        return base
    surfaces = state.pack.decode("edge_surface")
    factor = np.ones(len(surfaces))
    for surface, f in constraints.surface_cost_factors.items():
        factor[np.array([s == surface for s in surfaces])] = f
    arrays = dict(state.pack.arrays)
    for column in (
        ("edge_cost_foot", "edge_cost_foot_rev")
        if constraints.activity == "foot"
        else ("edge_cost_bike", "edge_cost_bike_rev")
    ):
        cost = arrays[column].copy()
        legal = cost >= 0
        cost[legal] = cost[legal] * factor[legal].astype(np.float32)
        arrays[column] = cost
    patched = Pack(manifest=state.pack.manifest, arrays=arrays)
    return Network.build(patched, constraints.activity)


# ── destinations ─────────────────────────────────────────────────────────────


def _destinations(
    pack: Pack,
    net: Network,
    constraints: Constraints,
    lon: float,
    lat: float,
    target_m: float,
) -> list[Destination]:
    from vaivia_routes.destinations import INTEREST

    wanted: set[str] = set()
    for w in constraints.waypoints:
        if w.role in ("end", "bathe", "eat"):
            wanted.update(w.kinds)
    if not wanted:
        wanted = set(INTEREST)

    kinds = pack.decode("place_kind")
    low, high = crow_band(target_m)
    kx, ky = _metric(lat)
    pool: list[Destination] = []
    for i in range(pack.counts["K"]):
        if pack["place_is_start"][i] or kinds[i] not in wanted:
            continue
        vertex = int(pack["place_vertex"][i])
        if pack["vertex_component"][vertex] != net.main_component:
            continue
        crow = math.hypot(
            kx * (pack["place_lon"][i] - lon), ky * (pack["place_lat"][i] - lat)
        )
        if not (low <= crow <= high):
            continue
        source = pack.decode("place_source")[i]
        pool.append(
            Destination(
                place_id=f"{source}:{pack['place_source_id'][i]}",
                kind=kinds[i],
                name=str(pack["place_name"][i]) or None,
                vertex_id=vertex,
                crow_m=crow,
            )
        )
    return rank(pool, top=SEEDS)


# ── candidates, rejection, relaxation ────────────────────────────────────────


def _collect(
    candidates: list[dict],
    counts: dict[str, int],
    walked,
    start_vertex: int,
    target_m: float,
    shape: str,
    destination: Destination | None = None,
) -> None:
    counts["drawn"] = counts.get("drawn", 0) + 1
    if walked is None:
        counts["unroutable"] = counts.get("unroutable", 0) + 1
        return
    assert_connected(walked)
    facts = assemble(walked)
    candidates.append(
        {
            "score": score(facts, target_m),
            "edge_ids": {e.edge_id for e in walked},
            "walked": walked,
            "facts": facts,
            "start_vertex": start_vertex,
            "target_m": target_m,
            "shape": shape,
            "destination": destination,
        }
    )


def _count(counts: dict[str, int], reason: str) -> bool:
    counts[reason] = counts.get(reason, 0) + 1
    return True


def _violates(
    facts: Assembled, c: Constraints, counts: dict[str, int], *, relaxed: bool
) -> bool:
    """The STATED limits, each rejection counted under its reason."""
    band = c.distance_band_m
    if band is not None:
        low, high = band
        if relaxed:
            low, high = low / RELAX_BAND, high * RELAX_BAND
        if not (low <= facts.distance_m <= high):
            return _count(
                counts, f"length outside {low / 1000:.0f}–{high / 1000:.0f} km"
            )
    if c.max_ascent_m is not None and (
        facts.ascent_m is None or facts.ascent_m > c.max_ascent_m
    ):
        return _count(counts, f"climb over {c.max_ascent_m:.0f} m (or unknown)")
    if (
        c.sac_cap is not None
        and facts.sac_max is not None
        and SAC_ORDER.index(facts.sac_max) > SAC_ORDER.index(c.sac_cap)
    ):
        return _count(counts, f"ground harder than {c.sac_cap}")
    if (
        c.mtb_cap is not None
        and facts.mtb_scale is not None
        and MTB_ORDER.index(facts.mtb_scale) > MTB_ORDER.index(c.mtb_cap)
    ):
        return _count(counts, f"trail harder than mtb:scale {c.mtb_cap}")
    if not relaxed:
        if (
            c.urban_share_max is not None
            and facts.urban_share is not None
            and facts.urban_share > c.urban_share_max
        ):
            return _count(counts, f"more than {c.urban_share_max:.0%} through town")
        if (
            c.urban_share_min is not None
            and facts.urban_share is not None
            and facts.urban_share < c.urban_share_min
        ):
            return _count(counts, "not enough of it in town")
        for surface, cap in c.surface_share_caps.items():
            if facts.surface.get(surface, 0.0) > cap:
                return _count(counts, f"more than {cap:.0%} {surface}")
    return False


def _reject_and_keep(
    candidates: list[dict], constraints: Constraints, out: PlannerResult
) -> list[dict]:
    surviving = [
        c
        for c in candidates
        if not _violates(c["facts"], constraints, out.counts, relaxed=False)
    ]
    if not surviving and candidates:
        # One rung: the preference caps lift, the band widens by half; the
        # safety caps stand. Every relaxed answer says so on the strip.
        relaxed_counts: dict[str, int] = {}
        surviving = [
            c
            for c in candidates
            if not _violates(c["facts"], constraints, relaxed_counts, relaxed=True)
        ]
        if surviving:
            out.relaxed = "nothing fit exactly — showing the nearest matches"
            out.assumptions.append(out.relaxed)
    return keep_distinct(surviving, max_keep=KEEP)


def _infeasible_clarify(counts: dict[str, int]) -> ClarifyIntent:
    """The Clarify is built FROM the counts: '42 in reach, all over 10 %
    asphalt' — never a shrug."""
    drawn = counts.get("drawn", 0)
    reasons = [
        (reason, n)
        for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])
        if reason not in ("drawn", "unroutable")
    ]
    if drawn and reasons:
        top = ", ".join(f"{n} had {reason}" for reason, n in reasons[:2])
        question = (
            f"I drew {drawn} candidates and none fit: {top}. "
            "Relax one of those, or start somewhere else?"
        )
    else:
        question = (
            "I could not draw a route for that ask from the starts in reach — "
            "try naming a town to start near, or asking for a different length."
        )
    return ClarifyIntent(
        question=question,
        suggestions=["allow some asphalt", "make it longer", "start near Lecco"],
    )


# ── cards and documents ──────────────────────────────────────────────────────


def _build_document(
    pack: Pack, entry: dict, constraints: Constraints, shape: str
) -> dict:
    facts: Assembled = entry["facts"]
    destination: Destination | None = entry["destination"]
    direction = None
    if shape in DIRECTED_SHAPES:
        direction = "fwd" if forward_is_stored(facts.coords) else "rev"
    rid = route_id([facts.coords], shape, direction or "fwd")

    lons = [x for x, _y in facts.coords]
    lats = [y for _x, y in facts.coords]
    bbox = [min(lons), min(lats), max(lons), max(lats)]

    return build_document(
        route_id=rid,
        kind="generated",
        shape=shape,
        direction=direction,
        identity={
            "name": route_name(destination) if destination else None,
            "ref": None,
            "activity": "mtb" if constraints.activity == "mtb" else "hiking",
            "network": None,
            "waymark": None,
            "from": None,
            "to": destination.name if destination else None,
            "operator": None,
            # The pack carries no per-edge regions; the reader that needs
            # them (R5's gazetteer) will bring them. Empty, not guessed.
            "regions": [],
            "osm_relation_id": None,
        },
        geometry={
            "type": "LineString",
            "coordinates": [[x, y] for x, y in facts.coords],
        },
        bbox=bbox,
        distance_m=facts.distance_m,
        ascent_m=facts.ascent_m,
        descent_m=facts.descent_m,
        lowest_m=min(facts.profile["elevation_m"]) if facts.profile else None,
        highest_m=max(facts.profile["elevation_m"]) if facts.profile else None,
        profile=facts.profile,
        surface_spans=[Span(e.surface, e.length_m) for e in entry["walked"]],
        sac_spans=[Span(e.sac_scale, e.length_m) for e in entry["walked"]],
        pieces=1,
        edges_without_profile=sum(1 for e in entry["walked"] if e.ascent_m is None),
        matched_fraction=None,
        places=_places_along(pack, facts.coords),
        terminals=[_terminal(pack, entry["start_vertex"])],
        provenance={
            "run_id": pack.run_id,
            "producer": "backend/chat/planner.py",
            "generation": {
                "activity": "mtb" if constraints.activity == "mtb" else "foot",
                "shape": shape,
                "destination": (
                    {
                        "id": destination.place_id,
                        "kind": destination.kind,
                        "name": destination.name,
                    }
                    if destination
                    else None
                ),
                "target_m": entry["target_m"],
                "seed": None,
                "score": entry["score"],
                "mtb_rideable": facts.mtb_rideable,
                "mtb_scale": facts.mtb_scale,
                "bike_blocked_m": round(facts.bike_blocked_m, 1),
                "off_road_share": round(facts.off_road_share, 3),
                "retrace_share": round(facts.retrace_share, 3),
                "urban_share": (
                    None if facts.urban_share is None else round(facts.urban_share, 3)
                ),
            },
            "sources": SOURCES,
        },
    )


def _places_along(pack: Pack, coords: list[tuple[float, float]]) -> list[dict]:
    """Pack places within PLACES_M of the line, ordered along it.

    Distance to the nearest route VERTEX, not the true segment distance —
    route vertices sit metres apart, so at a 100 m tolerance the
    approximation is noise. Bbox-prefiltered so the scan stays small.
    """
    lons = np.array([x for x, _ in coords])
    lats = np.array([y for _, y in coords])
    kx, ky = _metric(float(lats.mean()))
    pad = PLACES_M / METRES_PER_DEG_LAT * 2
    p_lon, p_lat = pack["place_lon"], pack["place_lat"]
    near = (
        (p_lon >= lons.min() - pad)
        & (p_lon <= lons.max() + pad)
        & (p_lat >= lats.min() - pad)
        & (p_lat <= lats.max() + pad)
    )
    step = np.concatenate(
        ([0.0], np.hypot(kx * np.diff(lons), ky * np.diff(lats)))
    ).cumsum()
    kinds = pack.decode("place_kind")
    sources = pack.decode("place_source")
    rows = []
    for i in np.flatnonzero(near):
        d = np.hypot(kx * (lons - p_lon[i]), ky * (lats - p_lat[i]))
        k = int(d.argmin())
        if d[k] > PLACES_M:
            continue
        ele = float(pack["place_ele_m"][i])
        rows.append(
            {
                "id": f"{sources[i]}:{pack['place_source_id'][i]}",
                "kind": kinds[i],
                "name": str(pack["place_name"][i]) or None,
                "ele_m": None if math.isnan(ele) else ele,
                "lon": round(float(p_lon[i]), 6),
                "lat": round(float(p_lat[i]), 6),
                "offset_m": round(float(d[k]), 1),
                "distance_along_m": round(float(step[k]), 1),
                "is_start": bool(pack["place_is_start"][i]),
            }
        )
    rows.sort(key=lambda r: (r["distance_along_m"], r["offset_m"]))
    return rows


def _terminal(pack: Pack, vertex: int) -> dict:
    at_vertex = np.flatnonzero(
        (pack["place_vertex"] == vertex) & pack["place_is_start"]
    )
    classes = pack.decode("place_start_class")
    names = sorted(
        {str(pack["place_name"][i]) for i in at_vertex if str(pack["place_name"][i])}
    )
    start_classes = sorted({classes[i] for i in at_vertex if classes[i]})
    return {
        "vertex_id": int(pack["vertex_id"][vertex]),
        "point": {
            "type": "Point",
            "coordinates": [
                float(pack["vertex_lon"][vertex]),
                float(pack["vertex_lat"][vertex]),
            ],
        },
        "names": names,
        "start_classes": start_classes,
        "car_free": bool({"station", "bus_stop"} & set(start_classes)),
        "nearest_start_m": 0.0,
        "reachable": True,
        # The pack terminal is unscoped in time; a season we cannot place is
        # always possible (the hazard rule, applied to arrivals).
        "seasons": {
            "spring": True,
            "summer": True,
            "autumn": True,
            "winter": True,
            "unverified": True,
        },
    }


def _card(entry: dict, document: dict, constraints: Constraints) -> dict:
    """The search_loops card shape, plus the geometry the map needs inline —
    a drawn route is in no catalogue, so there is nothing to fetch later."""
    facts: Assembled = entry["facts"]
    destination: Destination | None = entry["destination"]
    surface = shares(Span(e.surface, e.length_m) for e in entry["walked"])
    start_lon, start_lat = facts.coords[0]
    return {
        "id": document["id"],
        "activity": "mtb" if constraints.activity == "mtb" else "foot",
        "kind": "drawn",
        "shape": entry["shape"],
        "name": route_name(destination) if destination else None,
        "ref": None,
        "destination_name": destination.name if destination else None,
        "destination_kind": destination.kind if destination else None,
        "distance_m": round(facts.distance_m, 1),
        "ascent_m": facts.ascent_m,
        "descent_m": facts.descent_m,
        "lowest_m": min(facts.profile["elevation_m"]) if facts.profile else None,
        "highest_m": max(facts.profile["elevation_m"]) if facts.profile else None,
        "surface_dominant": dominant(surface),
        "surface": {k: round(v, 3) for k, v in surface.items()},
        "pieces": 1,
        "continuous": True,
        "sac_scale": facts.sac_scale,
        "sac_max": facts.sac_max,
        "graded_share": facts.graded_share,
        "mtb_rideable": facts.mtb_rideable,
        "mtb_scale": facts.mtb_scale,
        "bike_blocked_m": round(facts.bike_blocked_m, 1),
        "off_road_share": round(facts.off_road_share, 3),
        "score": entry["score"],
        "start_vertex_id": document["terminals"][0]["vertex_id"],
        "start_names": document["terminals"][0]["names"],
        "car_free": document["terminals"][0]["car_free"],
        "start_lat": start_lat,
        "start_lon": start_lon,
        "pois": [
            {"name": p["name"], "type": p["kind"]}
            for p in document["places"]
            if not p["is_start"]
        ][:8],
        "geometry": document["geometry"],
    }
