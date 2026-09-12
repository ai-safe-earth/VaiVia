"""Plan composer: atomic subqueries -> a deterministic execution plan.

The model decomposes a message into atomic subqueries (chat/intents.py). This
module — plain Python, no model in the loop — merges them into at most one
structured search, one semantic theme, and a bounded list of routes, each of
which the orchestrator maps onto a named parameterized template. When the plan
carries too little to search well, composition yields a clarification with
concrete suggestions instead of guessing. Nothing here ever builds query text.
"""

from dataclasses import dataclass, field

from chat.intents import (
    ClarifyIntent,
    Intent,
    LoopSearchIntent,
    OutingIntent,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)

MAX_SUBQUERIES = 4
MAX_ROUTES = 2

# A stated loop distance narrower than this fraction of itself is treated as a
# point estimate rather than a real interval, and widened.
NARROW_BAND_RATIO = 0.15
DISTANCE_TOLERANCE = 0.20

# Offered when the user gives us nothing actionable — each one is a complete
# question they can answer in a word or two, chosen to map onto an indexed
# filter or the vector index.
DEFAULT_SUGGESTIONS = [
    "Hiking or mountain biking?",
    "How long do you want to be out — say '2 hours' or 'under 15 km'?",
    "Any feature to pass — a lake, a hut, a viewpoint, somewhere to swim?",
    "Describe the mood — 'panoramic ridge', 'shady forest', 'lakeside gravel'",
]

# Offered when the user named an activity and nothing else. Each is a complete
# ask that lands on a different SHAPE of outing — a loop, an out-and-back, a
# named trail — so one tap both answers the guiding question and runs well.
GUIDED_SUGGESTIONS = {
    "hike": [
        "a loop hike of 10 to 15 km past a peak",
        "a hike to a rifugio and back",
        "an easy lakeside walk under 5 km",
    ],
    "mtb": [
        "a mountain bike loop under 800 m of climbing",
        "a 20 km ride past a viewpoint",
        "an easy gravel ride by the water",
    ],
}

_LIST_FIELDS = ("poi_types", "surface_exclusions", "exclude_hazards")
_MIN_OF_MAX = (
    "max_difficulty_level",
    "max_distance_m",
    "max_duration_min",
    "max_elevation_gain_m",
)
_MAX_OF_MIN = ("min_difficulty_level", "min_distance_m", "min_elevation_gain_m")
_FIRST_WINS = ("activity", "season", "region")


def sanitize(intent: TrailSearchIntent) -> TrailSearchIntent:
    """Drop vacuous bounds and filters the model sometimes emits instead of null.

    Strict structured outputs require every field, and despite the prompt the
    model occasionally writes 0 where it means "no limit" — a 0-metre
    max_distance_m would silently filter out every trail. A non-positive max
    and a zero min carry no information, so both become None.

    The same pressure makes it reach for activity="mixed" when the user implied
    no activity at all. As a filter that is the opposite of what it looks like:
    the template already matches 'mixed' trails against every activity, so
    "mixed" narrows the search to trails explicitly tagged both, while null
    matches those AND everything else. An unstated activity that arrives as
    "mixed" therefore returns fewer results than no filter — often none — so it
    becomes None. The cost is that a genuine "suitable for both" ask searches a
    little wider; that degrades gracefully, where the alternative returns
    nothing.
    """
    for name in _MIN_OF_MAX:
        value = getattr(intent, name)
        if value is not None and value <= 0:
            setattr(intent, name, None)
    for name in _MAX_OF_MIN:
        value = getattr(intent, name)
        if value is not None and value <= 0:
            setattr(intent, name, None)
    if intent.activity == "mixed":
        intent.activity = None
    # A difficulty bound at the end of its own scale admits every trail, so it
    # is not a constraint — it is the model writing "any difficulty" as
    # numbers. Dropping it matters beyond tidiness: an ask that is really just
    # an activity must LOOK like just an activity, or the guiding question for
    # vague asks (compose) never fires.
    if intent.min_difficulty_level == 1:
        intent.min_difficulty_level = None
    if intent.max_difficulty_level == 4:
        intent.max_difficulty_level = None
    return intent


@dataclass
class ComposedPlan:
    """What the orchestrator executes. Exactly one of clarify / work is set."""

    clarify: ClarifyIntent | None = None
    search: TrailSearchIntent | None = None
    theme: str | None = None
    routes: list[RouteIntent] = field(default_factory=list)
    loop: LoopSearchIntent | None = None
    #: The on-demand ask, verbatim. The planner consumes it when a pack is
    #: mounted; `outing_view` degrades it to a catalogue ask otherwise.
    outing: OutingIntent | None = None
    #: True when `loop` is the derived stand-in for `outing`, not an ask of
    #: its own — the planner suppresses it rather than answering twice.
    loop_from_outing: bool = False
    #: The typed, coverage-validated "from here" point, attached by the
    #: orchestrator AFTER extraction. Never dumped, never shown to a model.
    near: tuple[float, float] | None = None

    @property
    def is_clarify(self) -> bool:
        return self.clarify is not None


def has_constraints(intent: TrailSearchIntent) -> bool:
    """True when at least one filter differs from its default."""
    defaults = TrailSearchIntent()
    return any(
        getattr(intent, name) != getattr(defaults, name)
        for name in TrailSearchIntent.model_fields
        if name != "kind"
    )


def only_activity(intent: TrailSearchIntent) -> bool:
    """True when the activity is the ONLY thing the user gave us.

    "I want to hike" is in scope but not yet askable well: it would run an
    unbounded search and answer with whatever sorts first. That ask earns a
    guiding question instead (compose), so the distinction matters.
    """
    if intent.activity is None:
        return False
    defaults = TrailSearchIntent()
    return all(
        getattr(intent, name) == getattr(defaults, name)
        for name in TrailSearchIntent.model_fields
        if name not in ("kind", "activity")
    )


def capped_difficulty(max_level: int | None, family_friendly: bool) -> int | None:
    """The difficulty ceiling that actually runs.

    "with the kids" caps at 1 whatever else was said. That is a promise about
    children, and it was written out at both call sites that make it — the
    trail search and the catalogue view — where one of them could be changed
    alone.
    """
    if not family_friendly:
        return max_level
    return min(max_level or 1, 1)


def catalogue_view(search: TrailSearchIntent) -> LoopSearchIntent | None:
    """The same ask, posed to the route catalogue — or None when it cannot be.

    Owner rule (2026-08-21): a trail ask answers with BOTH kinds — named
    trails and complete outings from the catalogue — so a walker compares a
    loop against a sentiero without asking twice. The two stay distinguishable
    all the way to the screen; this only widens where the one ask is posed.

    The view exists only when every stated constraint can be honoured there.
    A constraint the catalogue cannot express — a season, a hazard, a surface,
    a difficulty floor or a climb floor (the template carries ceilings only) —
    must not silently return routes that ignore it: that is a lie shaped like
    a result. Those asks answer from the trail graph alone.

    Duration is the ratified exception (2026-08-21): the catalogue carries
    none until DIN 33466 is calibrated, and the explicit loop path already
    DROPS the duration filter rather than refusing to answer. The view does
    the same, so "a two hour hike" still sees the catalogue — described by
    its measured distance and climb, never by a figure nobody trusts.
    """
    if search.season is not None:
        return None
    if search.exclude_hazards or search.surface_exclusions:
        return None
    if search.min_difficulty_level is not None:
        return None
    if search.min_elevation_gain_m is not None:
        return None

    view = LoopSearchIntent(
        # sanitize() has already turned "mixed" into None; the guard is for
        # any caller that has not been through it.
        activity=search.activity if search.activity in ("hike", "mtb") else None,
        min_distance_m=search.min_distance_m,
        max_distance_m=search.max_distance_m,
        # A cap on climbing is a cap on ascent; the catalogue's word for it.
        max_ascent_m=search.max_elevation_gain_m,
        max_difficulty_level=search.max_difficulty_level,
        poi_types=list(search.poi_types),
        # A region name resolves the same way a start place does; a failed
        # resolution degrades to no geo filter rather than to zero results.
        near=search.region,
    )
    view.max_difficulty_level = capped_difficulty(
        view.max_difficulty_level, search.family_friendly
    )
    # The same widening merge_loops applies. "a 15 km hike" arrives as
    # min = max = 15000, which is an exact-equality filter over a catalogue
    # whose routes are 15,328 m long: without this the implicit block matches
    # nothing and silently vanishes, while "a 15 km loop" gets a band and
    # results. One ask, two phrasings, must reach the catalogue the same way.
    return widen_narrow_band(view)


#: OutingIntent waypoint kinds that trail search's PoiType also knows —
#: what the interim catalogue view can carry over.
_CATALOGUE_POI_KINDS = frozenset(
    {
        "lake",
        "hut",
        "campsite",
        "station",
        "bathing_water",
        "viewpoint",
        "peak",
        "saddle",
        "beach",
        "spring",
        "cave",
        "waterfall",
        "chapel",
        "castle",
        "ruins",
        "picnic_site",
    }
)


def outing_view(outing: OutingIntent) -> LoopSearchIntent | None:
    """The outing posed to the catalogue — the interim until R4's planner.

    Until the pack planner is wired into /chat, an outing must still answer
    with something honest: the closest catalogue ask, described by measured
    facts like every catalogue result. A multi-day ask has no one-day stand-in,
    so it degrades to None and the turn says nothing matched rather than
    offering day loops as a trek.
    """
    if outing.days > 1:
        return None
    view = LoopSearchIntent(
        activity="mtb" if outing.activity in ("mtb", "bike") else "hike",
        max_distance_m=(
            outing.max_distance_km * 1000
            if outing.max_distance_km is not None
            else None
        ),
        max_duration_min=(
            round(outing.max_hours * 60) if outing.max_hours is not None else None
        ),
        max_ascent_m=float(outing.max_ascent_m) if outing.max_ascent_m else None,
        poi_types=[k for k in outing.waypoint_kinds if k in _CATALOGUE_POI_KINDS],
        near=outing.start.name or outing.area,
    )
    if outing.party in ("kids", "small_kids"):
        view.max_difficulty_level = 1
    return widen_narrow_band(view)


def merge_searches(intents: list[TrailSearchIntent]) -> TrailSearchIntent:
    """Tightest-wins merge: every atomic constraint must hold in the result."""
    merged = TrailSearchIntent()
    for intent in intents:
        for name in _MIN_OF_MAX:
            values = [
                v
                for v in (getattr(merged, name), getattr(intent, name))
                if v is not None
            ]
            setattr(merged, name, min(values) if values else None)
        for name in _MAX_OF_MIN:
            values = [
                v
                for v in (getattr(merged, name), getattr(intent, name))
                if v is not None
            ]
            setattr(merged, name, max(values) if values else None)
        for name in _LIST_FIELDS:
            current = getattr(merged, name)
            for item in getattr(intent, name):
                if item not in current:
                    current.append(item)
        for name in _FIRST_WINS:
            if getattr(merged, name) is None:
                setattr(merged, name, getattr(intent, name))
        merged.family_friendly = merged.family_friendly or intent.family_friendly
    return merged


def merge_loops(loops: list[LoopSearchIntent]) -> LoopSearchIntent:
    """Tightest-wins merge, mirroring merge_searches."""
    merged = LoopSearchIntent()
    for loop in loops:
        for name in (
            "max_distance_m",
            "max_ascent_m",
            "max_difficulty_level",
            "max_duration_min",
        ):
            values = [
                v for v in (getattr(merged, name), getattr(loop, name)) if v is not None
            ]
            setattr(merged, name, min(values) if values else None)
        for name in ("min_distance_m",):
            values = [
                v for v in (getattr(merged, name), getattr(loop, name)) if v is not None
            ]
            setattr(merged, name, max(values) if values else None)
        for poi_type in loop.poi_types:
            if poi_type not in merged.poi_types:
                merged.poi_types.append(poi_type)
        merged.near = merged.near or loop.near
        merged.activity = merged.activity or loop.activity
        merged.avoid_roads = merged.avoid_roads or loop.avoid_roads
    # Same trap as the searches: a 0-metre max silently matches nothing.
    if merged.max_distance_m is not None and merged.max_distance_m <= 0:
        merged.max_distance_m = None
    if merged.min_distance_m is not None and merged.min_distance_m <= 0:
        merged.min_distance_m = None
    if merged.max_ascent_m is not None and merged.max_ascent_m <= 0:
        merged.max_ascent_m = None
    if merged.max_duration_min is not None and merged.max_duration_min <= 0:
        merged.max_duration_min = None
    return widen_narrow_band(merged)


def widen_narrow_band(loop: LoopSearchIntent) -> LoopSearchIntent:
    """A single stated distance is an approximation, so treat it as one.

    "a 15 km loop" comes back from the model as min=max=15000, which is an
    exact-equality filter. Real routes are 15,328 m, so that matches nothing
    and the user is told no such loop exists when 500 of them do. The model
    is not wrong about the number; it is the interval that needs saying, and
    saying it here keeps it deterministic rather than another prompt rule the
    model may or may not follow.
    """
    low, high = loop.min_distance_m, loop.max_distance_m
    if low is None or high is None or high < low:
        return loop
    midpoint = (low + high) / 2
    if midpoint <= 0:
        return loop
    if (high - low) / midpoint < NARROW_BAND_RATIO:
        loop.min_distance_m = midpoint * (1 - DISTANCE_TOLERANCE)
        loop.max_distance_m = midpoint * (1 + DISTANCE_TOLERANCE)
    return loop


def compose(subqueries: list[Intent]) -> ComposedPlan:
    """Merge atomic subqueries into one executable plan.

    Rules, in order:
      * an empty plan, or any clarify subquery, makes the whole turn a
        clarification (a partially-adversarial plan must not half-run);
      * structured searches merge tightest-wins; semantic themes join;
      * routes are kept in order, capped at MAX_ROUTES;
      * a search with no constraints and no theme and no routes is
        under-specified -> clarify with suggestions that drive a good search.
    """
    # The clarify scan reads the WHOLE plan, before the cap. A clarify poisons
    # the turn (CLAUDE.md), and truncating first meant a fifth subquery could
    # carry the refusal while the four runnable ones in front of it went to the
    # graph — the guarantee broken by an off-by-a-cap, on exactly the
    # adversarial input the guarantee exists for.
    clarifies = [s for s in subqueries if isinstance(s, ClarifyIntent)]
    subqueries = subqueries[:MAX_SUBQUERIES]

    if clarifies:
        first = clarifies[0]
        suggestions: list[str] = []
        for c in clarifies:
            for s in c.suggestions:
                if s not in suggestions:
                    suggestions.append(s)
        return ComposedPlan(
            clarify=ClarifyIntent(question=first.question, suggestions=suggestions[:4])
        )

    searches = [sanitize(s) for s in subqueries if isinstance(s, TrailSearchIntent)]
    themes = [s.text.strip() for s in subqueries if isinstance(s, SemanticThemeIntent)]
    themes = [t for t in themes if t]
    routes = [s for s in subqueries if isinstance(s, RouteIntent)][:MAX_ROUTES]
    loops = [s for s in subqueries if isinstance(s, LoopSearchIntent)]
    # Tightest-wins like the searches: two loop asks in one message are one
    # outing with both constraints, not two outings.
    loop = merge_loops(loops) if loops else None
    # One outing per turn: a message describes one outing, and two outing
    # subqueries are the model splitting what it should not — the first
    # speaks. (compile.py owns everything downstream of this.)
    outings = [s for s in subqueries if isinstance(s, OutingIntent)]
    outing = outings[0] if outings else None
    loop_from_outing = False
    if outing is not None and loop is None:
        loop = outing_view(outing)
        loop_from_outing = True

    search = merge_searches(searches) if searches else None
    theme = "; ".join(themes) if themes else None

    actionable = (
        bool(theme)
        or bool(routes)
        or loop is not None
        or outing is not None
        or (search is not None and has_constraints(search))
    )
    if not actionable:
        return ComposedPlan(
            clarify=ClarifyIntent(
                question=(
                    "I can search better with one more detail — "
                    "any of these would narrow it down:"
                ),
                suggestions=list(DEFAULT_SUGGESTIONS),
            )
        )

    # An activity alone is actionable but not askable WELL: it would run an
    # unbounded search and answer with whatever sorts first. Guide instead
    # (owner rule 2026-08-21): ask what shape of outing they want — a loop, an
    # out-and-back, a named trail — with suggestions they can tap, and query
    # once they have said. Deterministic Python, like every plan decision: the
    # model is not asked to decide when to ask.
    if (
        search is not None
        and only_activity(search)
        and theme is None
        and not routes
        and loop is None
        and outing is None
    ):
        return ComposedPlan(
            clarify=ClarifyIntent(
                question=(
                    "Happy to pick a good one — what shape of outing? "
                    "A loop that comes back to the start, somewhere worth "
                    "walking to and back, or a named trail. And roughly "
                    "how far?"
                ),
                suggestions=list(
                    GUIDED_SUGGESTIONS.get(search.activity or "", DEFAULT_SUGGESTIONS)
                ),
            )
        )

    return ComposedPlan(
        search=search,
        theme=theme,
        routes=routes,
        loop=loop,
        outing=outing,
        loop_from_outing=loop_from_outing,
    )


# ── The standing plan ────────────────────────────────────────────────────────
# A conversation's constraints in force, persisted per assistant turn in
# messages.intent["standing"] and merged with each refinement turn's delta.
# Latest-wins, never tightest-wins: "actually, longer" must RAISE a max where
# merge_searches would keep the old tighter one. Python end to end — the model
# only says WHICH constraints changed (PlanEnvelope.refine + the delta
# subqueries); what they change is decided here.

#: Field names shared by TrailSearchIntent and LoopSearchIntent that a
#: cross-kind delta may carry over. `activity` is excluded: the two intents
#: spell it in different vocabularies, and a wrong guess there flips which
#: catalogue is searched.
_CROSS_FIELDS = (
    "min_distance_m",
    "max_distance_m",
    "max_duration_min",
    "max_difficulty_level",
    "poi_types",
)


def _is_set(intent: TrailSearchIntent | LoopSearchIntent, name: str) -> bool:
    """Did the model actually emit this field? Strict outputs force every
    field to appear, so "set" means "differs from the default" — the same
    reading has_constraints uses. The known ceiling: a delta cannot RESET a
    field to its default (family_friendly back to False, "any difficulty").
    """
    # ponytail: deltas can change but not clear a constraint; upgrade path is
    # an explicit cleared_fields list on the envelope if review shows the need.
    default = type(intent).model_fields[name].get_default(call_default_factory=True)
    return getattr(intent, name) != default


def _overlay(base, delta):
    """Same-kind merge: every field the delta set replaces the base's."""
    merged = base.model_copy()
    for name in type(base).model_fields:
        if name != "kind" and _is_set(delta, name):
            setattr(merged, name, getattr(delta, name))
    return merged


def _cross_overlay(base, delta):
    """Cross-kind merge: "shorter" against a standing loop often arrives as a
    trail_search (or vice versa). The shared constraint names carry over; the
    base keeps its kind, so the same catalogue answers."""
    merged = base.model_copy()
    for name in _CROSS_FIELDS:
        if name in type(delta).model_fields and _is_set(delta, name):
            setattr(merged, name, getattr(delta, name))
    return merged


def apply_delta(standing: ComposedPlan, delta: ComposedPlan) -> ComposedPlan:
    """Merge a refinement turn's delta onto the conversation's standing plan.

    Only called when the model set `refine` and this turn is not a clarify
    (a clarify poisons the turn before it gets here). A route ask with nothing
    else is a change of subject — "now route me from A to B" — and starts
    fresh even under a stray refine flag.

    Standing ROUTES are never carried: a route is a one-shot answer, not a
    constraint in force, and carrying it re-ran "Lecco to Bergamo" under
    every later ask of the conversation (owner session, 2026-08-26).
    """
    if standing.is_clarify:
        return delta
    if delta.routes and not (delta.search or delta.loop or delta.theme):
        return delta

    merged = ComposedPlan(
        search=standing.search,
        theme=standing.theme,
        loop=standing.loop,
        outing=standing.outing,
    )
    if delta.outing is not None:
        merged.outing = (
            _overlay(merged.outing, delta.outing)
            if merged.outing is not None
            else delta.outing
        )
        # The interim catalogue view tracks the outing it was derived from;
        # an explicit loop delta below still wins.
        if delta.loop is None:
            merged.loop = outing_view(merged.outing)
            merged.loop_from_outing = True
    if delta.search is not None:
        if merged.search is not None:
            merged.search = _overlay(merged.search, delta.search)
        elif merged.loop is not None and delta.loop is None:
            merged.loop = _cross_overlay(merged.loop, delta.search)
        else:
            merged.search = delta.search
    if delta.loop is not None:
        if merged.loop is not None:
            merged.loop = _overlay(merged.loop, delta.loop)
        elif merged.search is not None and delta.search is None:
            merged.search = _cross_overlay(merged.search, delta.loop)
        else:
            merged.loop = delta.loop
    if delta.theme:
        merged.theme = delta.theme
    if delta.routes:
        merged.routes = list(delta.routes)
    return merged


def standing_dump(plan: ComposedPlan) -> dict | None:
    """The executed plan as the jsonb the next turn reloads. None for clarify
    (a question in force is not a plan in force)."""
    if plan.is_clarify:
        return None
    return {
        "search": plan.search.model_dump() if plan.search else None,
        "loop": plan.loop.model_dump() if plan.loop else None,
        "outing": plan.outing.model_dump() if plan.outing else None,
        "theme": plan.theme,
        "routes": [r.model_dump() for r in plan.routes],
    }


def standing_load(data: dict | None) -> ComposedPlan | None:
    """The inverse, defensive: jsonb written by an older build, or by nothing,
    must degrade to "no standing plan", never to a crash mid-turn."""
    if not isinstance(data, dict):
        return None
    try:
        plan = ComposedPlan(
            search=(
                TrailSearchIntent.model_validate(data["search"])
                if data.get("search")
                else None
            ),
            loop=(
                LoopSearchIntent.model_validate(data["loop"])
                if data.get("loop")
                else None
            ),
            outing=(
                OutingIntent.model_validate(data["outing"])
                if data.get("outing")
                else None
            ),
            theme=data.get("theme") or None,
            routes=[RouteIntent.model_validate(r) for r in data.get("routes") or []],
        )
    except Exception:  # noqa: BLE001 — malformed history is "no standing plan"
        return None
    if not (plan.search or plan.loop or plan.theme or plan.routes or plan.outing):
        return None
    return plan
