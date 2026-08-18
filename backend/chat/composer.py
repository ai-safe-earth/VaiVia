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
    return intent


@dataclass
class ComposedPlan:
    """What the orchestrator executes. Exactly one of clarify / work is set."""

    clarify: ClarifyIntent | None = None
    search: TrailSearchIntent | None = None
    theme: str | None = None
    routes: list[RouteIntent] = field(default_factory=list)
    loop: LoopSearchIntent | None = None

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
        for name in ("max_distance_m", "max_ascent_m", "max_difficulty_level"):
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
    subqueries = subqueries[:MAX_SUBQUERIES]

    clarifies = [s for s in subqueries if isinstance(s, ClarifyIntent)]
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

    search = merge_searches(searches) if searches else None
    theme = "; ".join(themes) if themes else None

    actionable = (
        bool(theme)
        or bool(routes)
        or loop is not None
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

    return ComposedPlan(search=search, theme=theme, routes=routes, loop=loop)
