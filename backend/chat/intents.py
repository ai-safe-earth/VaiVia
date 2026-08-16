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

from api.models import Activity, PoiType, Season

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
    TrailSearchIntent | RouteIntent | SemanticThemeIntent | ClarifyIntent,
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
    """

    subqueries: list[Intent] = Field(default_factory=list)


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
