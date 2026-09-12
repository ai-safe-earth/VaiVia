"""The LLM boundary.

The model's ONLY structured output is one of these validated intents. It never
writes Cypher, never names a query template, and never supplies a database
identifier. Each intent maps — in Python, not in the model — onto a
parameterized template from graph/queries.cypher.

Anything the model cannot express as a valid intent becomes Clarify, which runs
no query at all. That is the containment property: a prompt-injection payload
can at worst produce a Clarify or a harmless search.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from api.models import Activity, PoiKind, PoiType, Season

DIFFICULTY_LEVELS = {"Easy": 1, "Intermediate": 2, "Difficult": 3, "Pro": 4}


class TrailSearchIntent(BaseModel):
    """Find trails matching constraints. Maps to the search_trails template."""

    kind: Literal["trail_search"] = "trail_search"
    activity: Activity | None = None
    min_difficulty_level: Annotated[int, Field(ge=1, le=4)] | None = None
    max_difficulty_level: Annotated[int, Field(ge=1, le=4)] | None = None
    min_distance_m: Annotated[float, Field(ge=0)] | None = None
    max_distance_m: Annotated[float, Field(ge=0)] | None = None
    max_duration_min: Annotated[int, Field(ge=0)] | None = None
    min_elevation_gain_m: Annotated[float, Field(ge=0)] | None = None
    max_elevation_gain_m: Annotated[float, Field(ge=0)] | None = None
    poi_types: list[PoiType] = Field(default_factory=list)
    surface_exclusions: list[str] = Field(default_factory=list)
    season: Season | None = None
    exclude_hazards: list[str] = Field(default_factory=list)
    region: str | None = None
    family_friendly: bool = False


class LoopSearchIntent(BaseModel):
    """A circular outing: start somewhere you can reach, come back to it.

    Distinct from TrailSearchIntent (a named trail with properties) and from
    RouteIntent (getting from one named place to another). It selects from the
    precomputed catalogue the pipeline exports, so no field here names
    a template, an id, or anything the model could steer the query with -- only
    what a walker would say out loud.
    """

    kind: Literal["loop_search"] = "loop_search"
    #: Catalogues are generated per activity, so this selects which one is
    #: searched rather than filtering one shared set.
    activity: Literal["hike", "mtb"] | None = None
    min_distance_m: Annotated[float, Field(ge=0)] | None = None
    max_distance_m: Annotated[float, Field(ge=0)] | None = None
    #: "a two hour loop". Durations are estimates from a cautious model, so
    #: this filters on our figure, not on a promise.
    max_duration_min: Annotated[int, Field(ge=0)] | None = None
    #: "nothing too steep", "under 800 m of climbing".
    max_ascent_m: Annotated[float, Field(ge=0)] | None = None
    #: 1 easy .. 4 hardest, mapped to sac_scale / mtb:scale by the orchestrator.
    max_difficulty_level: Annotated[int, Field(ge=1, le=4)] | None = None
    poi_types: list[PoiType] = Field(default_factory=list)
    #: A place to start near, by name. Resolved server-side against known POIs.
    near: str | None = None
    #: "on trails", "keep off the roads". Maps to a floor on off-road share.
    avoid_roads: bool = False


class Waypoint(BaseModel):
    """Somewhere the outing should pass, end at, or use — with the ROLE the
    walker gave it ("ending at a lake to bathe"). No coordinates, no ids:
    a kind and/or a name, resolved server-side like every other place."""

    kind: PoiKind | None = None
    name: str | None = None
    role: Literal["pass", "end", "bathe", "eat", "sleep"] = "pass"


class StartSpec(BaseModel):
    """Where the outing begins. `here` is resolved from ChatRequest.near —
    typed, validated to coverage, attached AFTER extraction; the model never
    sees or emits a coordinate."""

    mode: Literal["here", "named", "station", "parking", "any"] = "any"
    name: str | None = None
    max_drive_min: Annotated[int, Field(ge=0, le=600)] | None = None
    car_free: bool = False


class OutingIntent(BaseModel):
    """An outing to DRAW on demand over the pack (docs/route-design.md),
    rather than a search over named trails or the catalogue.

    No field carries a query, a template name, a database id, a coordinate
    or a weight. chat/compile.py — Python, not the model — owns every number
    the planner sees.
    """

    kind: Literal["outing"] = "outing"
    activity: Literal["hike", "walk", "mtb", "bike"]
    shape: Literal["loop", "out_and_back", "destination", "traverse"] | None = None
    party: Literal["solo", "adults", "kids", "small_kids"] | None = None
    fitness: Literal["easy", "moderate", "challenging", "expert"] | None = None
    days: Annotated[int, Field(ge=1, le=14)] = 1
    min_hours: Annotated[float, Field(ge=0, le=24)] | None = None
    max_hours: Annotated[float, Field(ge=0, le=24)] | None = None
    max_distance_km: Annotated[float, Field(ge=0)] | None = None
    max_ascent_m: Annotated[int, Field(ge=0)] | None = None
    waypoints: list[Waypoint] = Field(default_factory=list)
    surface_exclusions: list[Literal["asphalt", "paved", "gravel"]] = Field(
        default_factory=list
    )
    setting: Literal["nature", "mixed", "town"] | None = None
    theme: Literal["cultural", "panoramic", "water", "forest"] | None = None
    start: StartSpec = Field(default_factory=StartSpec)
    sleep: Literal["hut", "campsite", "agriturismo", "wild", "any"] | None = None
    area: str | None = None
    #: Ordinal of a card earlier in this conversation ("make the second one
    #: shorter") — an ordinal, never an id.
    refine_from: Annotated[int, Field(ge=1, le=20)] | None = None

    @property
    def waypoint_kinds(self) -> list[str]:
        return [w.kind for w in self.waypoints if w.kind]

    @property
    def waypoint_roles(self) -> list[str]:
        return [w.role for w in self.waypoints]


class RouteIntent(BaseModel):
    """Route between two named places. Maps to the routing template chain."""

    kind: Literal["route"] = "route"
    start: str
    end: str
    max_distance_m: Annotated[float, Field(gt=0)] | None = None


class SemanticThemeIntent(BaseModel):
    """Free-text atmosphere the structured filters cannot express
    ("panoramic ridge", "shady forest by a stream"). The text is embedded
    server-side and handed to the vector index as a parameter — it never
    approaches Cypher."""

    kind: Literal["semantic_theme"] = "semantic_theme"
    text: str


class ClarifyIntent(BaseModel):
    """Out of scope, ambiguous, or adversarial — ask instead of guessing.
    Suggestions are short example follow-ups that would make a good search."""

    kind: Literal["clarify"] = "clarify"
    question: str
    suggestions: list[str] = Field(default_factory=list)


Intent = Annotated[
    TrailSearchIntent
    | LoopSearchIntent
    | OutingIntent
    | RouteIntent
    | SemanticThemeIntent
    | ClarifyIntent,
    Field(discriminator="kind"),
]


class IntentEnvelope(BaseModel):
    """Legacy single-intent envelope (kept for tools that probe one intent)."""

    intent: Intent


class PlanEnvelope(BaseModel):
    """What the model is asked to return: the user's message decomposed into
    atomic subqueries. The composer (chat/composer.py) — Python, not the model —
    merges these into named parameterized templates. Length is deliberately
    unconstrained here (strict mode rejects array bounds); the composer caps it.

    `refine` marks the message as a MODIFICATION of the conversation's standing
    plan ("shorter", "easier than that") rather than a self-contained ask. The
    subqueries then carry only the changed constraints, and the composer merges
    them onto the persisted plan (apply_delta) — in Python, like every other
    plan decision. A bool: it carries no query, template or identifier, so the
    containment property is untouched.
    """

    subqueries: list[Intent] = Field(default_factory=list)
    refine: bool = False
    #: "start over", "forget that", "delete the constraints": the standing
    #: plan is DISCARDED before this turn is considered. The counterpart of
    #: refine, and the only way to clear a constraint (a delta can change one
    #: but not unset it — apply_delta's known ceiling).
    reset: bool = False


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> OpenAI strict structured-output schema.

    Strict mode requires every property listed in `required` and
    `additionalProperties: false` on every object, so optional fields must be
    expressed as nullable rather than omitted. It also rejects `oneOf` and the
    `discriminator` keyword, which Pydantic emits for tagged unions — unions
    must be expressed as `anyOf`. The `kind` literal still discriminates on our
    side when validating the response.
    """
    schema = model.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if "oneOf" in node:
                node["anyOf"] = node.pop("oneOf")
            node.pop("discriminator", None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                properties = node.get("properties", {})
                node["required"] = list(properties)
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for item in node:
                tighten(item)

    tighten(schema)
    return schema


def difficulty_from_label(label: str) -> int | None:
    return DIFFICULTY_LEVELS.get(label.strip().title())
