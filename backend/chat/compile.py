"""Compile an OutingIntent into the geometric constraints the planner runs.

This module owns EVERY number the planner sees (docs/route-design.md): the
model says what the walker said, and the translation into metres, caps and
factors happens here, deterministically, where a test can pin it. Pure
Python — no I/O, no model, no store.

The numbers are PRODUCT estimates, not derived constants — same posture as
core/durations.py. Each translation that involves a judgement is written
into `assumptions`, in the walker's language, and the frontend shows that
strip ("we read ~3 h as 12–18 km for kids on bikes") so a wrong reading is
visible and correctable rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chat.intents import ClarifyIntent, OutingIntent

# ── Pace (km/h, moving) ──────────────────────────────────────────────────────
# Base by activity; multiplied by the party factor. Hike matches
# core/durations.py's calibrated flat rate; bike paces are family-outing
# paces, not sport paces. The reference case pinned by test_compile.py is
# ask A: ~3 h for kids on bikes reads as 12–18 km (docs/route-design.md).
PACE_KMH = {"hike": 4.0, "walk": 3.5, "mtb": 10.0, "bike": 14.0}
PARTY_PACE_FACTOR = {"solo": 1.1, "adults": 1.0, "kids": 0.36, "small_kids": 0.25}

#: A stated time is an approximation, so the distance it implies is a band.
DISTANCE_TOLERANCE = 0.20

# ── Climb (m/h of ascent a party sustains) ───────────────────────────────────
# The hike rate is core/durations.py's calibrated 450; wheels climb slower
# per metre of ascent than legs on these trails. At most half the time out
# is spent climbing — the other half is the flat and the way down.
ASCENT_MH = {"hike": 450.0, "walk": 350.0, "mtb": 300.0, "bike": 250.0}
PARTY_ASCENT_FACTOR = {"solo": 1.1, "adults": 1.0, "kids": 0.5, "small_kids": 0.3}
CLIMB_TIME_SHARE = 0.5

# ── Party → technical caps ───────────────────────────────────────────────────
# SAC grades as in the route documents; None = no cap. Children stay on
# hiking-grade ground; mtb:scale 0 is a smooth trail.
PARTY_SAC_CAP = {
    "solo": None,
    "adults": None,
    "kids": "hiking",
    "small_kids": "hiking",
}
PARTY_MTB_CAP = {"solo": None, "adults": None, "kids": "1", "small_kids": "0"}

# ── Setting → urban share ────────────────────────────────────────────────────
URBAN_CAP_NATURE = 0.25
URBAN_FLOOR_TOWN = 0.40

# ── Surface exclusions ───────────────────────────────────────────────────────
# An exclusion is a strong preference, not a wall: cost ×4 steers the router
# off the surface, and the share cap rejects what still could not avoid it.
SURFACE_COST_FACTOR = 4.0
SURFACE_SHARE_CAP = 0.10

# ── Waypoint roles → place kinds ─────────────────────────────────────────────
# A role without a kind means any of these; a stated kind narrows it.
ROLE_KINDS = {
    "bathe": ("lake", "beach", "river_access", "bathing_water"),
    "eat": ("hut", "agriturismo"),
    "sleep": ("hut", "campsite", "agriturismo"),
}
SLEEP_KINDS = {
    "hut": ("hut",),
    "campsite": ("campsite",),
    "agriturismo": ("agriturismo",),
    "wild": (),
    "any": ("hut", "campsite", "agriturismo"),
}

# ── Coverage ─────────────────────────────────────────────────────────────────
# The gazetteer (R5) will carry polygons; until then coverage is a name set.
# Lowercased membership; unlisted → a Python Clarify naming what IS covered.
COVERED_AREAS = frozenset(
    {
        "lecco",
        "bergamo",
        "orobie",
        "grigna",
        "grigne",
        "grignetta",
        "resegone",
        "valsassina",
        "val brembana",
        "val seriana",
        "lake como",
        "lago di como",
        "como",  # the lake's east shore is in coverage; the city is its edge
    }
)
COVERAGE_ANSWER = "the mountains around Lecco and Bergamo (Grigne, Resegone, Orobie)"


@dataclass
class CompiledWaypoint:
    kinds: tuple[str, ...]
    name: str | None
    role: str


def find_area(gazetteer: list[dict], area: str) -> dict | None:
    """The gazetteer entry an area name means, article stripped, by alias."""
    key = _area_key(area)
    for entry in gazetteer:
        if key == entry["name"].lower() or key in (entry.get("aliases") or ()):
            return entry
    return None


@dataclass
class Constraints:
    """What the planner receives: bands, caps and factors — numbers only,
    every one of them minted here."""

    activity: str  # planner cost layer: foot or bike
    shape: str | None
    days: int
    distance_band_m: tuple[float, float] | None = None
    max_ascent_m: float | None = None
    sac_cap: str | None = None
    mtb_cap: str | None = None
    urban_share_max: float | None = None
    urban_share_min: float | None = None
    #: The asked area's coarse polygon (lon/lat ring) from the gazetteer —
    #: the planner keeps candidate starts inside it.
    area_polygon: list | None = None
    area_name: str | None = None
    #: surface -> cost multiplier the router applies
    surface_cost_factors: dict[str, float] = field(default_factory=dict)
    #: surface -> hard share cap the reject step counts against
    surface_share_caps: dict[str, float] = field(default_factory=dict)
    waypoints: list[CompiledWaypoint] = field(default_factory=list)
    sleep_kinds: tuple[str, ...] = ()
    start_mode: str = "any"
    start_name: str | None = None
    max_drive_min: int | None = None
    car_free: bool = False
    #: The judgements made, in the walker's language — the assumptions strip.
    assumptions: list[str] = field(default_factory=list)


#: The planner's two cost layers (network.COST_COLUMNS): legs or wheels.
COST_LAYER = {"hike": "foot", "walk": "foot", "mtb": "mtb", "bike": "mtb"}


def _pace_kmh(intent: OutingIntent) -> float:
    return PACE_KMH[intent.activity] * PARTY_PACE_FACTOR.get(intent.party or "", 1.0)


def _band_from_hours(intent: OutingIntent) -> tuple[float, float] | None:
    """hours × activity × party → a distance band in metres, ±20 %."""
    hours = [h for h in (intent.min_hours, intent.max_hours) if h]
    if not hours:
        return None
    pace = _pace_kmh(intent)
    low = min(hours) * pace * 1000 * (1 - DISTANCE_TOLERANCE)
    high = max(hours) * pace * 1000 * (1 + DISTANCE_TOLERANCE)
    return (low, high)


#: Leading articles the model sometimes keeps ("the Orobie", "le Grigne").
_ARTICLES = ("the ", "il ", "lo ", "la ", "le ", "i ", "gli ", "l'")


def _area_key(area: str) -> str:
    key = area.strip().lower()
    for article in _ARTICLES:
        if key.startswith(article):
            return key[len(article) :].strip()
    return key


def compile_outing(
    intent: OutingIntent, gazetteer: list[dict] | None = None
) -> Constraints | ClarifyIntent:
    """The intent's words as the planner's numbers — or a Clarify when the
    ask leaves our coverage. A Clarify here is Python's, not the model's:
    an area we do not cover must be said, never approximated (ask E).

    With a pack mounted, coverage is the gazetteer it ships (R5): a covered
    area brings its polygon, an uncovered or unknown one an honest refusal
    naming what IS covered. The static name set stays as the no-pack
    fallback."""
    area_entry: dict | None = None
    if intent.area:
        area = intent.area.strip()
        if gazetteer is not None:
            area_entry = find_area(gazetteer, area)
            if area_entry is None or not area_entry.get("covered"):
                covered = ", ".join(e["name"] for e in gazetteer if e.get("covered"))
                return ClarifyIntent(
                    question=(
                        f"We do not cover {area} yet — VaiVia knows "
                        f"{covered}. Want an outing there instead?"
                    ),
                    suggestions=[
                        "three days hut to hut in the Orobie",
                        "a loop in the Grigne",
                        "a lakeside ride near Lecco",
                    ],
                )
        elif _area_key(area) not in COVERED_AREAS:
            return ClarifyIntent(
                question=(
                    f"We do not cover {area} yet — VaiVia knows "
                    f"{COVERAGE_ANSWER}. Want an outing there instead?"
                ),
                suggestions=[
                    "three days hut to hut in the Orobie",
                    "a loop in the Grigne",
                    "a lakeside ride near Lecco",
                ],
            )

    out = Constraints(
        activity=COST_LAYER[intent.activity],
        shape=intent.shape,
        days=intent.days,
        area_polygon=(area_entry or {}).get("polygon"),
        area_name=(area_entry or {}).get("name") or (intent.area or None),
        start_mode=intent.start.mode,
        start_name=intent.start.name,
        max_drive_min=intent.start.max_drive_min,
        car_free=intent.start.car_free,
    )

    # Distance: a stated km cap wins; otherwise hours imply a band.
    band = _band_from_hours(intent)
    if intent.max_distance_km is not None:
        out.distance_band_m = (0.0, intent.max_distance_km * 1000)
    elif band is not None:
        out.distance_band_m = band
        per_day = "" if intent.days == 1 else " a day"
        who = f" for {intent.party.replace('_', ' ')}" if intent.party else ""
        hours = intent.max_hours or intent.min_hours
        out.assumptions.append(
            f"we read ~{hours:g} h{per_day}{who} on "
            f"{'bikes' if out.activity == 'mtb' else 'foot'} as "
            f"{band[0] / 1000:.0f}–{band[1] / 1000:.0f} km{per_day} (our estimate)"
        )

    # Ascent: a stated cap wins; otherwise hours bound the climb too.
    if intent.max_ascent_m is not None:
        out.max_ascent_m = float(intent.max_ascent_m)
    elif intent.max_hours:
        rate = ASCENT_MH[intent.activity] * PARTY_ASCENT_FACTOR.get(
            intent.party or "", 1.0
        )
        out.max_ascent_m = round(intent.max_hours * CLIMB_TIME_SHARE * rate)
        out.assumptions.append(
            f"climb capped at ~{out.max_ascent_m:.0f} m (our estimate)"
        )

    if intent.party:
        out.sac_cap = PARTY_SAC_CAP[intent.party]
        out.mtb_cap = PARTY_MTB_CAP[intent.party]
        if out.sac_cap or out.mtb_cap:
            out.assumptions.append(
                f"kept to easy ground for {intent.party.replace('_', ' ')}"
            )

    if intent.setting == "nature":
        out.urban_share_max = URBAN_CAP_NATURE
    elif intent.setting == "town":
        out.urban_share_min = URBAN_FLOOR_TOWN

    for surface in intent.surface_exclusions:
        out.surface_cost_factors[surface] = SURFACE_COST_FACTOR
        out.surface_share_caps[surface] = SURFACE_SHARE_CAP
    if intent.surface_exclusions:
        out.assumptions.append(
            f"avoiding {', '.join(intent.surface_exclusions)} "
            f"(at most {SURFACE_SHARE_CAP:.0%} of the way)"
        )

    for w in intent.waypoints:
        # A role with a semantic set (bathe/eat/sleep) UNIONS a stated kind
        # rather than being replaced by it: "a lake or river to bathe"
        # arriving as kind=bathing_water must not narrow the ask to the one
        # kind the model guessed — the role says what the walker meant.
        role_kinds = ROLE_KINDS.get(w.role, ())
        kinds = tuple(dict.fromkeys(((w.kind,) if w.kind else ()) + role_kinds))
        out.waypoints.append(CompiledWaypoint(kinds=kinds, name=w.name, role=w.role))

    if intent.days > 1:
        out.sleep_kinds = SLEEP_KINDS[intent.sleep or "any"]

    return out
