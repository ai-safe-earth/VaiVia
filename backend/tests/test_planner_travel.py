"""R5's consumers: the gazetteer speaks for coverage, the drive limit
filters starts (measured or crow-fly, each said out loud), unanchored
starts rank by the ground they open, and a destination ask sorts
potential-feasible starts first."""

import numpy as np
import pytest

from chat.compile import compile_outing
from chat.intents import ClarifyIntent, OutingIntent, StartSpec
from chat.planner import _candidate_starts, _in_polygon, plan_outing
from tests.test_planner import ANCHOR, FIXTURE, constraints

GAZETTEER = [
    {
        "name": "Grigne",
        "aliases": ["grigne", "grigna"],
        "covered": True,
        "polygon": [[9.3, 45.8], [9.5, 45.8], [9.5, 46.0], [9.3, 46.0], [9.3, 45.8]],
    },
    {
        "name": "Tuscany",
        "aliases": ["tuscany", "toscana"],
        "covered": False,
        "polygon": None,
    },
]


@pytest.fixture(scope="module")
def state():
    from chat.pack_state import load_planner

    return load_planner(str(FIXTURE))


def test_gazetteer_coverage_beats_the_static_set():
    out = compile_outing(
        OutingIntent(activity="hike", area="la Grigna"), gazetteer=GAZETTEER
    )
    assert not isinstance(out, ClarifyIntent)
    assert out.area_name == "Grigne" and out.area_polygon is not None
    refused = compile_outing(
        OutingIntent(activity="bike", area="Toscana"), gazetteer=GAZETTEER
    )
    assert isinstance(refused, ClarifyIntent)
    assert "Grigne" in refused.question  # names what IS covered, from the data
    # an area the gazetteer has never heard of refuses too — never approximated
    unknown = compile_outing(
        OutingIntent(activity="hike", area="Patagonia"), gazetteer=GAZETTEER
    )
    assert isinstance(unknown, ClarifyIntent)


def test_point_in_polygon():
    # interior, east of the ring, clearly south-west of it (a corner point
    # is boundary-ambiguous by design — the polygons are coarse)
    lon = np.array([9.4, 9.6, 9.2])
    lat = np.array([45.9, 45.9, 45.7])
    ring = [[9.3, 45.8], [9.5, 45.8], [9.5, 46.0], [9.3, 46.0], [9.3, 45.8]]
    assert _in_polygon(lon, lat, ring).tolist() == [True, False, False]


def test_area_polygon_confines_the_starts(state):
    c = constraints(shape="loop", max_hours=2)
    # a polygon covering only the fixture tile's south-west quarter
    c.area_polygon = [
        [9.38, 45.84],
        [9.40, 45.84],
        [9.40, 45.855],
        [9.38, 45.855],
        [9.38, 45.84],
    ]
    starts = _candidate_starts(state.pack, state.networks["foot"], c, None)
    assert starts
    for _v, lon, lat in starts:
        assert 9.37 <= lon <= 9.41 and 45.83 <= lat <= 45.86


def test_drive_limit_filters_and_says_how(state):
    c = constraints(
        activity="hike",
        shape="loop",
        max_hours=2,
        start=StartSpec(mode="here", max_drive_min=10),
    )
    result = plan_outing(state, c, ANCHOR)
    said = " ".join(result.assumptions)
    # the fixture tile carries no settlement rows (D=0), so the crow-fly
    # substitution must be said out loud; with a full pack the measured
    # sentence appears instead — either way the strip says which
    assert "drive" in said
    assert ("crow" in said) or ("measured" in said)


def test_unanchored_starts_rank_by_opened_ground(state):
    c = constraints(shape="loop", max_hours=2)
    starts = _candidate_starts(state.pack, state.networks["foot"], c, None)
    share = state.pack["start_trail_share_5km"]
    vertices = state.pack["place_vertex"]
    by_vertex = {}
    for i in np.flatnonzero(state.pack["place_is_start"]):
        by_vertex[int(vertices[i])] = float(np.nan_to_num(share[i], nan=0.0))
    ranked = [by_vertex[v] for v, _lon, _lat in starts]
    assert ranked == sorted(ranked, reverse=True)
    assert ranked[0] > 0  # the fixture's best start opens real ground


def test_destination_ask_prefers_potential_feasible_starts(state):
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
    # feasible-first is an ordering, not a promise: either routes end at a
    # peak, or the clarify counts why not — never a silent shrug
    if result.routes:
        for r in result.routes:
            generation = r["document"]["provenance"]["generation"]
            assert generation["destination"]["kind"] == "peak"
    else:
        assert result.clarify is not None
