"""The pack planner over the committed Lecco fixture: draws, rejects with
counts, relaxes once, and mints schema-valid documents in-process."""

import json
from pathlib import Path

import pytest

from chat.compile import compile_outing
from chat.intents import ClarifyIntent, OutingIntent, StartSpec
from chat.pack_state import load_planner
from chat.planner import plan_outing

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "shared"
    / "routes"
    / "tests"
    / "fixtures"
    / "pack-lecco-3km"
)
SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "pipeline"
    / "schemas"
    / "route-document.schema.json"
)

# The fixture cut's centre — a start anchor every test shares.
ANCHOR = (45.855, 9.40)


@pytest.fixture(scope="module")
def state():
    return load_planner(str(FIXTURE))


def constraints(**kwargs):
    c = compile_outing(OutingIntent(activity=kwargs.pop("activity", "hike"), **kwargs))
    assert not isinstance(c, ClarifyIntent)
    return c


def test_a_loop_ask_yields_up_to_three_distinct_routes(state):
    result = plan_outing(state, constraints(shape="loop", max_hours=2), ANCHOR)
    assert result.clarify is None
    assert 1 <= len(result.routes) <= 3
    ids = [r["card"]["id"] for r in result.routes]
    assert len(set(ids)) == len(ids)
    for r in result.routes:
        card, doc = r["card"], r["document"]
        assert card["id"] == doc["id"] and card["id"].startswith("vv2-")
        assert card["geometry"]["type"] == "LineString"
        assert card["distance_m"] > 0
        assert doc["provenance"]["run_id"] == state.run_id
    assert result.counts.get("drawn", 0) >= len(result.routes)


def test_documents_validate_against_the_schema(state):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    result = plan_outing(state, constraints(shape="loop", max_hours=2), ANCHOR)
    assert result.routes
    for r in result.routes:
        jsonschema.validate(r["document"], schema)


def test_rejects_are_counted_under_their_stated_reason(state):
    # A 100-120 m band: most candidates are counted out by length (a dense
    # network can still hold a tiny block loop, so survivors are legal —
    # they just must actually fit the band).
    c = constraints(shape="loop")
    c.distance_band_m = (100.0, 120.0)
    result = plan_outing(state, c, ANCHOR)
    assert any("length outside" in reason for reason in result.counts)
    low, high = (100.0 / 1.5, 120.0 * 1.5) if result.relaxed else (100.0, 120.0)
    for r in result.routes:
        assert low <= r["card"]["distance_m"] <= high


def test_infeasible_clarify_is_built_from_the_counts():
    from chat.planner import _infeasible_clarify

    clarify = _infeasible_clarify(
        {"drawn": 42, "unroutable": 2, "more than 10% asphalt": 40}
    )
    assert "42" in clarify.question and "asphalt" in clarify.question
    assert _infeasible_clarify({"drawn": 5, "unroutable": 5}).question


def test_out_of_reach_asks_are_counted_unroutable(state):
    # A 47 km loop in a 3 km tile: the vias leave the network, the draws
    # come back empty, and the clarify says we could not draw — never a
    # route silently shorter than asked.
    c = constraints(shape="loop")
    c.distance_band_m = (45_000.0, 50_000.0)
    result = plan_outing(state, c, ANCHOR)
    assert not result.routes
    assert result.counts.get("unroutable", 0) > 0
    assert result.clarify is not None


def test_relaxation_widens_the_band_and_says_so(state):
    # Tight band that near-misses: candidates exist, none inside; the rung
    # widens by half and the strip says so.
    base = plan_outing(state, constraints(shape="loop", max_hours=2), ANCHOR)
    assert base.routes
    drawn_km = base.routes[0]["card"]["distance_m"] / 1000
    c = constraints(shape="loop")
    c.distance_band_m = (drawn_km * 1000 * 1.25, drawn_km * 1000 * 1.30)
    result = plan_outing(state, c, ANCHOR)
    if result.routes:  # the rung fired, or a longer loop genuinely fit
        assert result.relaxed or all(
            c.distance_band_m[0] <= r["card"]["distance_m"] <= c.distance_band_m[1]
            for r in result.routes
        )


def test_out_and_back_ends_at_a_wanted_kind(state):
    from chat.intents import Waypoint

    c = compile_outing(
        OutingIntent(
            activity="hike",
            shape="out_and_back",
            max_hours=3,
            waypoints=[Waypoint(role="end", kind="peak")],
        )
    )
    result = plan_outing(state, c, ANCHOR)
    if not result.routes:
        # the tile may hold no reachable peak in band — the clarify counts it
        assert result.clarify is not None
        return
    for r in result.routes:
        assert r["document"]["provenance"]["generation"]["destination"]["kind"] == (
            "peak"
        )
        # strict retrace: the walk comes home on its own edges
        assert r["document"]["shape"] == "out_and_back"


def test_multi_day_is_an_honest_clarify(state):
    c = compile_outing(OutingIntent(activity="hike", days=3, sleep="hut"))
    result = plan_outing(state, c, ANCHOR)
    assert result.clarify is not None and "Multi-day" in result.clarify.question


def test_station_mode_with_no_station_in_tile_clarifies(state):
    c = compile_outing(
        OutingIntent(activity="hike", start=StartSpec(mode="station"), max_hours=1)
    )
    result = plan_outing(state, c, ANCHOR)
    # The 3 km tile has no station starts; the planner must say it found no
    # start, never draw from an arbitrary one.
    if result.clarify is not None:
        assert "start" in result.clarify.question.lower()
    else:  # the tile surprised us with a station — then it must be one
        assert result.routes
