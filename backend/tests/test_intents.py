"""Intent schema: the containment boundary must hold at the type level."""

import pytest
from pydantic import ValidationError

from chat.intents import (
    ClarifyIntent,
    IntentEnvelope,
    PlanEnvelope,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
    to_strict_schema,
)


def test_plan_envelope_discriminates_each_subquery():
    envelope = PlanEnvelope.model_validate(
        {
            "subqueries": [
                {"kind": "trail_search", "activity": "hike"},
                {"kind": "semantic_theme", "text": "panoramic ridge"},
                {"kind": "route", "start": "Lecco", "end": "Rifugio"},
            ]
        }
    )
    kinds = [type(s) for s in envelope.subqueries]
    assert kinds == [TrailSearchIntent, SemanticThemeIntent, RouteIntent]


def test_plan_rejects_unknown_subquery_kind():
    with pytest.raises(ValidationError):
        PlanEnvelope.model_validate({"subqueries": [{"kind": "raw_cypher"}]})


def test_plan_subquery_cannot_smuggle_extra_fields():
    envelope = PlanEnvelope.model_validate(
        {
            "subqueries": [
                {
                    "kind": "semantic_theme",
                    "text": "lakeside",
                    "cypher": "MATCH (n) DETACH DELETE n",
                    "template": "search_trails",
                }
            ]
        }
    )
    subquery = envelope.subqueries[0]
    assert not hasattr(subquery, "cypher")
    assert not hasattr(subquery, "template")
    assert set(subquery.model_dump()) == {"kind", "text"}


def test_plan_strict_schema_is_openai_safe():
    import json

    serialized = json.dumps(to_strict_schema(PlanEnvelope))
    assert "oneOf" not in serialized
    assert "discriminator" not in serialized
    assert "anyOf" in serialized


def test_envelope_discriminates_on_kind():
    envelope = IntentEnvelope.model_validate(
        {"intent": {"kind": "route", "start": "Lecco", "end": "Rifugio"}}
    )
    assert isinstance(envelope.intent, RouteIntent)


def test_unknown_intent_kind_is_rejected():
    with pytest.raises(ValidationError):
        IntentEnvelope.model_validate({"intent": {"kind": "drop_database"}})


def test_model_cannot_smuggle_extra_fields():
    """An injected 'cypher' or 'query' field must not survive validation."""
    envelope = IntentEnvelope.model_validate(
        {
            "intent": {
                "kind": "trail_search",
                "activity": "mtb",
                "cypher": "MATCH (n) DETACH DELETE n",
                "query": "anything",
            }
        }
    )
    assert not hasattr(envelope.intent, "cypher")
    assert not hasattr(envelope.intent, "query")
    assert (
        envelope.intent.model_dump().keys() == TrailSearchIntent().model_dump().keys()
    )


def test_invalid_difficulty_is_rejected():
    with pytest.raises(ValidationError):
        TrailSearchIntent(max_difficulty_level=7)


def test_unknown_poi_type_is_rejected():
    with pytest.raises(ValidationError):
        TrailSearchIntent(poi_types=["casino"])


def test_negative_distance_is_rejected():
    with pytest.raises(ValidationError):
        TrailSearchIntent(min_distance_m=-1)


def test_clarify_requires_a_question():
    with pytest.raises(ValidationError):
        ClarifyIntent()


def test_strict_schema_has_no_oneof_or_discriminator():
    """OpenAI strict mode rejects both; Pydantic emits them for tagged unions."""
    import json

    serialized = json.dumps(to_strict_schema(IntentEnvelope))
    assert "oneOf" not in serialized
    assert "discriminator" not in serialized
    assert "anyOf" in serialized  # the union survives, in the permitted form


def test_strict_schema_marks_every_property_required_and_closed():
    schema = to_strict_schema(IntentEnvelope)

    def check(node):
        if isinstance(node, dict):
            if "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(schema)
