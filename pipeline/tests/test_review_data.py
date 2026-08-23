"""The state notebook's rules. No database — pure functions.

Everything here decides how a number is presented or which feature a reviewer
is shown first, which is exactly the kind of judgement that goes wrong quietly:
a legend that sorts alphabetically, a count printed as a measurement, a
"representative example" that is merely the first row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "notebooks"))

import review_data as rd


def test_a_count_reads_as_a_count():
    """Postgres returns count() as numeric, which arrives as a float, and
    "101,951.0 edges" reads like a measurement rather than a tally."""
    assert rd.number(101951.0) == "101,951"
    assert rd.number(8110) == "8,110"


def test_a_measurement_keeps_its_decimals():
    assert rd.number(98.4) == "98.4"
    assert rd.number(9238.5) == "9,238.5"


def test_categories_sort_by_their_prefix_not_their_word():
    """The leading digit is the whole point of the *_class convention: without
    it a legend puts flat between gentle and moderate."""
    values = pd.Series(
        [
            "3 moderate (15-30%)",
            "1 flat (<5%)",
            "9 unknown",
            "2 gentle (5-15%)",
            "1 flat (<5%)",
        ]
    )

    assert rd.ordered_categories(values) == [
        "1 flat (<5%)",
        "2 gentle (5-15%)",
        "3 moderate (15-30%)",
        "9 unknown",
    ]


def test_a_two_way_split_gets_the_two_way_palette():
    """`0 onto a trail` against `1 onto a lane` is a judgement, not a scale, so
    it reads better as two colours than as two steps of a ramp."""
    colours = rd.category_colours(pd.Series(["0 onto a trail", "1 onto a lane"]))

    assert list(colours.values()) == [rd.BLUE, rd.RED]


def test_a_scale_keeps_its_order_and_never_runs_out_of_colours():
    values = pd.Series([f"{i} step" for i in range(8)])
    colours = rd.category_colours(values)

    assert list(colours) == rd.ordered_categories(values)
    assert len(colours) == 8


def test_nulls_are_not_a_category():
    values = pd.Series(["0 named", None, "1 unnamed"])

    assert rd.ordered_categories(values) == ["0 named", "1 unnamed"]


def test_the_example_shown_is_the_one_that_matters():
    """The findings side carries no class columns on purpose — a duplicate, a
    bridge and a shared stretch look identical to a rule — so which one to look
    at first is a sort on the raw measure."""
    findings = pd.DataFrame({"finding_id": [1, 2, 3], "shared_m": [12.0, 83.0, 4.0]})

    assert rd.worst(findings, "shared_m")["finding_id"].tolist() == [2]
    assert rd.worst(findings, "shared_m", 2)["finding_id"].tolist() == [2, 1]


def test_a_window_is_padded_in_metres_not_degrees():
    """A degree of longitude at 46 N is about 77 km against latitude's 111, so
    padding both axes by the same number of degrees leaves the window a third
    short east-west."""
    min_lon, min_lat, max_lon, max_lat = rd.window(Point(9.39, 45.86), 1000.0)

    assert max_lon - min_lon > max_lat - min_lat
    # ...and it really is about a kilometre each way, not a degree.
    assert (max_lat - min_lat) / 2 == pytest.approx(1000 / 111_320, rel=0.05)


def test_a_postgres_array_becomes_something_a_legend_can_print():
    assert rd.as_text(["Lecco", "Bergamo"]) == "Lecco, Bergamo"
    assert rd.as_text([None]) == ""
    assert rd.as_text(None) == ""


def test_the_tolerance_binds_the_rules_that_move_ground():
    """gap_dangle_edge splits an edge and degenerate collapses one; neither
    moves anything, so their end_moved_m is the piece that resulted. Charting
    all four against the 2 m tolerance claims a repair moved something 555 m."""
    assert set(rd.SNAPPING_RULES) == {"gap_dangle_pair", "gap_dangle_junction"}


def test_the_metrics_are_the_bundle_s_metrics():
    """Imported, never restated: if a definition is wrong it should be wrong in
    one place rather than disagree with the generated review."""
    from export import review_bundle

    assert rd._METRICS["state"] is review_bundle.STATE
    assert rd._METRICS["issues"] is review_bundle.ISSUES
    assert rd._METRICS["settled"] is review_bundle.SETTLED


def test_a_route_with_no_ref_is_not_labelled_nan():
    """A missing text column arrives as NaN once pandas has it, and str(nan) is
    "nan" — which is how the Via Mercatorum ended up in a legend as
    "nan — Via Mercatorum"."""
    assert rd.as_text(float("nan")) == ""
    assert rd.route_label(
        {"ref": float("nan"), "name": "Via Mercatorum", "rel_id": 1}
    ) == ("Via Mercatorum")


def test_a_route_is_called_by_its_ref_and_name_together():
    row = {"ref": "DOL", "name": "Dorsale Orobica Lecchese", "rel_id": 9}

    assert rd.route_label(row) == "DOL — Dorsale Orobica Lecchese"


def test_a_route_with_neither_is_named_by_its_relation():
    """650 of 752 carry a ref and 273 a name; inventing one for the rest is the
    decision nobody has made for the trailheads either."""
    row = {"ref": None, "name": None, "rel_id": 4271}

    assert rd.route_label(row) == "relation 4271"


def test_the_crossing_routes_are_their_own_area():
    """61 of 752 run through both regions, and forcing them into whichever holds
    more of them would draw them twice or drop them once."""
    assert rd.AREA_ORDER == ["Lecco", "Bergamo", "Lecco + Bergamo"]


def _edge(coords, profile, length_m):
    return {"geom": LineString(coords), "profile_m": profile, "length_m": length_m}


def _edges(*rows):
    return gpd.GeoDataFrame(list(rows), geometry="geom", crs=4326)


def test_a_profile_runs_the_length_of_the_route():
    edges = _edges(
        _edge([(0, 0), (0, 1)], [100.0, 200.0], 1000.0),
        _edge([(0, 1), (0, 2)], [200.0, 300.0], 1000.0),
    )

    distances, heights = rd.assemble_profile(edges)

    assert distances[0] == 0.0
    assert distances[-1] == pytest.approx(2.0)  # km
    assert heights == [100.0, 200.0, 200.0, 300.0]


def test_an_edge_drawn_backwards_is_turned_round():
    """A relation lists its ways in walking order, but each way keeps the
    direction it was drawn in. Concatenating the samples as stored puts a cliff
    in the profile that is not on the hill."""
    edges = _edges(
        _edge([(0, 0), (0, 1)], [100.0, 200.0], 1000.0),
        # same ground, drawn the other way: its samples start at the far end
        _edge([(0, 2), (0, 1)], [300.0, 200.0], 1000.0),
    )

    _, heights = rd.assemble_profile(edges)

    assert heights == [100.0, 200.0, 200.0, 300.0]


def test_the_first_edge_is_taken_as_it_comes():
    """Nothing precedes it, so there is nothing to orient it against — the
    walking direction of a route is the mapper's business, not this
    function's."""
    edges = _edges(_edge([(0, 1), (0, 0)], [200.0, 100.0], 1000.0))

    _, heights = rd.assemble_profile(edges)

    assert heights == [200.0, 100.0]


def test_an_edge_with_nothing_sampled_is_skipped_not_guessed():
    edges = _edges(
        _edge([(0, 0), (0, 1)], [100.0, 200.0], 1000.0),
        _edge([(0, 1), (0, 2)], None, 1000.0),
        _edge([(0, 2), (0, 3)], [300.0, 400.0], 1000.0),
    )

    distances, heights = rd.assemble_profile(edges)

    assert heights == [100.0, 200.0, 300.0, 400.0]
    # the skipped edge's ground is not invented, and the walk still passed it
    assert distances[-1] == pytest.approx(3.0)


def test_a_gap_between_pieces_is_not_bridged():
    """Distance keeps running because the walk does; the profile stays honest
    about not knowing what is in between."""
    edges = _edges(
        _edge([(0, 0), (0, 1)], [100.0, 200.0], 1000.0),
        _edge([(5, 5), (5, 6)], [900.0, 950.0], 1000.0),
    )

    distances, heights = rd.assemble_profile(edges)

    assert heights == [100.0, 200.0, 900.0, 950.0]
    assert distances[2] == pytest.approx(1.0)
