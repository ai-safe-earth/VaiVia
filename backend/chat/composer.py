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
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)

MAX_SUBQUERIES = 4
MAX_ROUTES = 2

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
    """Drop vacuous bounds the model sometimes emits instead of null.

    Strict structured outputs require every field, and despite the prompt the
    model occasionally writes 0 where it means "no limit" — a 0-metre
    max_distance_m would silently filter out every trail. A non-positive max
    and a zero min carry no information, so both become None.
    """
    for name in _MIN_OF_MAX:
        value = getattr(intent, name)
        if value is not None and value <= 0:
            setattr(intent, name, None)
    for name in _MAX_OF_MIN:
        value = getattr(intent, name)
        if value is not None and value <= 0:
            setattr(intent, name, None)
    return intent


@dataclass
class ComposedPlan:
    """What the orchestrator executes. Exactly one of clarify / work is set."""

    clarify: ClarifyIntent | None = None
    search: TrailSearchIntent | None = None
    theme: str | None = None
    routes: list[RouteIntent] = field(default_factory=list)

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

    search = merge_searches(searches) if searches else None
    theme = "; ".join(themes) if themes else None

    actionable = (
        bool(theme) or bool(routes) or (search is not None and has_constraints(search))
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

    return ComposedPlan(search=search, theme=theme, routes=routes)
