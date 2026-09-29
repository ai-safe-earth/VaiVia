"""Plan composer: atomic subqueries -> a deterministic execution plan.

The model decomposes a message into atomic subqueries (chat/intents.py). This
module — plain Python, no model in the loop — merges them into at most one
structured search, one semantic theme and one outing to draw; the
orchestrator maps the search onto a named parameterized template and hands
the outing to the planner. When the plan
carries too little to search well, composition yields a clarification with
concrete suggestions instead of guessing. Nothing here ever builds query text.
"""

from dataclasses import dataclass

from chat.intents import (
    ClarifyIntent,
    Intent,
    OutingIntent,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)

MAX_SUBQUERIES = 4

# A stated loop distance narrower than this fraction of itself is treated as a
# point estimate rather than a real interval, and widened.

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


#: Words too common to prove a place was said ("Lago di Como" is said by
#: "como", never by "di").
_PLACE_FILLER = frozenset({"lago", "lake", "monte", "the", "del", "della", "di"})


def _said(place: str, message: str) -> bool:
    """Did the user's own words name this place? Whole value, or any
    distinctive word of it: "Mandello" says "Mandello del Lario", "the
    Orobie" says "Orobie", "Lecco station" says "Lecco"."""
    text = message.lower()
    value = place.strip().lower()
    if not value:
        return True
    if value in text:
        return True
    return any(
        len(word) >= 4 and word not in _PLACE_FILLER and word in text
        for word in value.replace("'", " ").split()
    )


def drop_unsaid_places(subqueries: list[Intent], message: str) -> list[Intent]:
    """A place the message never names is dropped, in Python.

    The prompt forbids carrying a place from an earlier turn into this one
    ("a bike route of less than 20 km" after "... near Bergamo" has NO
    region), and the model still does it — measured on golden g32/g34 when
    the named-place examples moved. A refinement is no exception: its delta
    carries only what changed, and a place that changed was said. The
    standing plan keeps its places through apply_delta, never through the
    model. Named start, named end, area and region all obey it.
    """
    out: list[Intent] = []
    for s in subqueries:
        if (
            isinstance(s, TrailSearchIntent)
            and s.region
            and not _said(s.region, message)
        ):
            s = s.model_copy(update={"region": None})
        elif isinstance(s, RouteIntent):
            update = {
                end: None
                for end in ("start", "end")
                if getattr(s, end) and not _said(getattr(s, end), message)
            }
            if update:
                s = s.model_copy(update=update)
        elif isinstance(s, OutingIntent):
            update: dict = {}
            if s.area and not _said(s.area, message):
                update["area"] = None
            if s.start.name and not _said(s.start.name, message):
                start = s.start.model_copy(update={"name": None})
                if start.mode == "named":
                    start.mode = "any"
                update["start"] = start
            kept = []
            for w in s.waypoints:
                if w.name and not _said(w.name, message):
                    if w.kind is None and w.role in ("end", "pass"):
                        continue  # a nameless, kindless waypoint says nothing
                    w = w.model_copy(update={"name": None})
                kept.append(w)
            if len(kept) != len(s.waypoints) or any(
                a is not b for a, b in zip(kept, s.waypoints, strict=True)
            ):
                update["waypoints"] = kept
            if update:
                s = s.model_copy(update=update)
        out.append(s)
    return out


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
    #: The on-demand ask, verbatim, drawn by the planner over the pack.
    outing: OutingIntent | None = None
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
    children, kept in one place so no call site can drop it.
    """
    if not family_friendly:
        return max_level
    return min(max_level or 1, 1)


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
      * one outing per turn — an A-to-B ask is an outing with a named end
        (docs/route-design.md decision 4), drawn over the pack;
      * a search with no constraints and no theme and no outing is
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
    # One outing per turn: a message describes one outing, and two outing
    # subqueries are the model splitting what it should not — the first
    # speaks. (compile.py owns everything downstream of this.)
    outings = [s for s in subqueries if isinstance(s, OutingIntent)]
    if not outings:
        # An A-to-B ask arrives as a route and is drawn as an outing: the
        # model keeps the kind it reads best, Python decides what runs.
        outings = [route_as_outing(s) for s in subqueries if isinstance(s, RouteIntent)]
    outing = _without_start_waypoint(outings[0]) if outings else None

    search = merge_searches(searches) if searches else None
    theme = "; ".join(themes) if themes else None

    actionable = (
        bool(theme)
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
        outing=outing,
    )


# ── The standing plan ────────────────────────────────────────────────────────
# A conversation's constraints in force, persisted per assistant turn in
# messages.intent["standing"] and merged with each refinement turn's delta.
# Latest-wins, never tightest-wins: "actually, longer" must RAISE a max where
# merge_searches would keep the old tighter one. Python end to end — the model
# only says WHICH constraints changed (PlanEnvelope.refine + the delta
# subqueries); what they change is decided here.


def _is_set(intent: TrailSearchIntent | OutingIntent, name: str) -> bool:
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


#: What the model writes into a route end when the user named none.
_NO_PLACE = frozenset({"", "named", "here", "any", "unknown", "none", "start"})


def route_as_outing(route: RouteIntent) -> OutingIntent:
    """The A-to-B ask as the outing the pack draws: the start named (or
    "here"), the end a named waypoint, there and back. No end is a loop from
    the start; no start lets the planner pick trailheads near the end."""
    start = (route.start or "").strip()
    end = (route.end or "").strip()
    if start.lower() in ("here", "my location", "current location"):
        spec = {"mode": "here"}
    elif start.lower() in _NO_PLACE:
        spec = {"mode": "any"}
    else:
        spec = {"mode": "named", "name": start}
    waypoints = [] if end.lower() in _NO_PLACE else [{"name": end, "role": "end"}]
    return OutingIntent(
        activity="hike",
        start=spec,
        waypoints=waypoints,
        shape=None if waypoints else "loop",
        max_distance_km=(
            route.max_distance_m / 1000 if route.max_distance_m is not None else None
        ),
    )


def _without_start_waypoint(outing: OutingIntent) -> OutingIntent:
    """The named start is never also a waypoint. The model repeats it ("from
    Lecco to Abbadia" -> a waypoint Lecco beside start Lecco), and a later
    turn that replaces the start would otherwise keep Lecco as a place to
    pass (g34)."""
    start = " ".join((outing.start.name or "").lower().split())
    if not start:
        return outing
    kept = [
        w
        for w in outing.waypoints
        if not (w.name and " ".join(w.name.lower().split()) == start)
    ]
    if len(kept) == len(outing.waypoints):
        return outing
    return outing.model_copy(update={"waypoints": kept})


def _settle_ends(merged: OutingIntent, delta: OutingIntent) -> OutingIntent:
    """Where a refined outing ends, decided in Python.

    A loop ends where it starts, so a turn that SAYS loop drops the standing
    end ("to Abbadia", then "a 10 km loop" — g34). A turn that names a new
    end on a standing loop makes it a there-and-back instead.
    """
    waypoints = list(merged.waypoints)
    shape = merged.shape
    if delta.shape == "loop":
        waypoints = [w for w in waypoints if w.role != "end"]
    elif shape == "loop" and any(w.role == "end" and w.name for w in delta.waypoints):
        shape = None
    return _without_start_waypoint(
        merged.model_copy(update={"waypoints": waypoints, "shape": shape})
    )


def apply_delta(standing: ComposedPlan, delta: ComposedPlan) -> ComposedPlan:
    """Merge a refinement turn's delta onto the conversation's standing plan.

    Only called when the model set `refine` and this turn is not a clarify
    (a clarify poisons the turn before it gets here).
    """
    if standing.is_clarify:
        return delta

    merged = ComposedPlan(
        search=standing.search,
        theme=standing.theme,
        outing=standing.outing,
    )
    if delta.outing is not None:
        merged.outing = (
            _overlay(merged.outing, delta.outing)
            if merged.outing is not None
            else delta.outing
        )
        merged.outing = _settle_ends(merged.outing, delta.outing)
    if delta.search is not None:
        merged.search = (
            _overlay(merged.search, delta.search)
            if merged.search is not None
            else delta.search
        )
    if delta.theme:
        merged.theme = delta.theme
    return merged


def standing_dump(plan: ComposedPlan) -> dict | None:
    """The executed plan as the jsonb the next turn reloads. None for clarify
    (a question in force is not a plan in force)."""
    if plan.is_clarify:
        return None
    return {
        "search": plan.search.model_dump() if plan.search else None,
        "outing": plan.outing.model_dump() if plan.outing else None,
        "theme": plan.theme,
    }


def standing_load(data: dict | None) -> ComposedPlan | None:
    """The inverse, defensive: jsonb written by an older build, or by nothing,
    must degrade to "no standing plan", never to a crash mid-turn. A "routes"
    key from before RouteIntent was retired is ignored — a route was never a
    constraint in force."""
    if not isinstance(data, dict):
        return None
    try:
        plan = ComposedPlan(
            search=(
                TrailSearchIntent.model_validate(data["search"])
                if data.get("search")
                else None
            ),
            outing=(
                OutingIntent.model_validate(data["outing"])
                if data.get("outing")
                else None
            ),
            theme=data.get("theme") or None,
        )
    except Exception:  # noqa: BLE001 — malformed history is "no standing plan"
        return None
    if not (plan.search or plan.theme or plan.outing):
        return None
    return plan
