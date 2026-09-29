"""Composer: atomic subqueries merge deterministically, and thin plans clarify."""

from chat.composer import (
    MAX_SUBQUERIES,
    ComposedPlan,
    apply_delta,
    compose,
    has_constraints,
    merge_searches,
    only_activity,
    standing_dump,
    standing_load,
)
from chat.intents import (
    ClarifyIntent,
    OutingIntent,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)


def test_single_search_passes_through():
    plan = compose([TrailSearchIntent(activity="mtb", max_distance_m=20000)])
    assert not plan.is_clarify
    assert plan.search is not None and plan.search.activity == "mtb"
    assert plan.theme is None
    assert plan.outing is None


def test_merge_takes_the_tightest_bound_of_each_constraint():
    merged = merge_searches(
        [
            TrailSearchIntent(max_distance_m=20000, min_distance_m=2000),
            TrailSearchIntent(max_distance_m=12000, min_distance_m=5000),
        ]
    )
    assert merged.max_distance_m == 12000  # min of maxes
    assert merged.min_distance_m == 5000  # max of mins


def test_merge_unions_list_filters_and_ors_family_friendly():
    merged = merge_searches(
        [
            TrailSearchIntent(poi_types=["lake"], family_friendly=True),
            TrailSearchIntent(poi_types=["lake", "hut"], exclude_hazards=["ice"]),
        ]
    )
    assert merged.poi_types == ["lake", "hut"]
    assert merged.exclude_hazards == ["ice"]
    assert merged.family_friendly is True


def test_themes_join_and_searches_merge():
    plan = compose(
        [
            SemanticThemeIntent(text="panoramic ridge"),
            SemanticThemeIntent(text="wildflowers"),
            TrailSearchIntent(max_difficulty_level=2),
        ]
    )
    assert plan.theme == "panoramic ridge; wildflowers"
    assert plan.search is not None and plan.search.max_difficulty_level == 2


def test_any_clarify_poisons_the_plan():
    plan = compose(
        [
            TrailSearchIntent(activity="mtb"),
            ClarifyIntent(question="Which area?", suggestions=["near Lecco"]),
        ]
    )
    assert plan.is_clarify
    assert plan.clarify is not None
    assert plan.clarify.suggestions == ["near Lecco"]
    assert plan.search is None and plan.outing is None


def test_empty_plan_clarifies_with_suggestions():
    plan = compose([])
    assert plan.is_clarify
    assert plan.clarify is not None and len(plan.clarify.suggestions) > 0


def test_constraint_free_search_alone_clarifies():
    plan = compose([TrailSearchIntent()])
    assert plan.is_clarify


def test_theme_alone_is_actionable():
    plan = compose([SemanticThemeIntent(text="shady forest by a stream")])
    assert not plan.is_clarify
    assert plan.theme == "shady forest by a stream"


def test_blank_theme_is_not_actionable():
    plan = compose([SemanticThemeIntent(text="   ")])
    assert plan.is_clarify


def test_an_a_to_b_ask_is_an_outing_with_a_named_end():
    """RouteIntent is retired: "from Ponteranica to Canto Alto" is an outing
    whose start is named and whose end is a named waypoint, drawn over the
    pack (docs/route-design.md decision 4)."""
    outing = OutingIntent(
        activity="hike",
        start={"mode": "named", "name": "Ponteranica"},
        waypoints=[{"name": "Canto Alto", "role": "end"}],
    )
    plan = compose([outing])
    assert not plan.is_clarify
    assert plan.outing is not None
    assert plan.outing.start.name == "Ponteranica"
    assert plan.outing.waypoint_names == ["Canto Alto"]


def test_the_first_outing_speaks():
    plan = compose(
        [
            OutingIntent(activity="hike", waypoints=[{"name": "a", "role": "end"}]),
            OutingIntent(activity="hike", waypoints=[{"name": "b", "role": "end"}]),
        ]
    )
    assert plan.outing is not None and plan.outing.waypoint_names == ["a"]


def test_a_clarify_past_the_cap_still_poisons_the_plan():
    """The cap bounds what RUNS; it must not bound what refuses.

    This test used to assert the opposite — that a fifth subquery carrying the
    clarify was simply dropped — which enshrined the one case the guarantee
    exists for: adversarial input arriving behind four runnable subqueries and
    reaching the graph anyway.
    """
    subqueries = [
        TrailSearchIntent(activity="mtb"),
        SemanticThemeIntent(text="ridge"),
        OutingIntent(activity="hike"),
        OutingIntent(activity="mtb"),
        ClarifyIntent(question="?"),  # fifth: past MAX_SUBQUERIES, still heard
    ]
    assert len(subqueries) == MAX_SUBQUERIES + 1
    plan = compose(subqueries)
    assert plan.is_clarify
    assert plan.search is None and plan.outing is None and plan.theme is None


def test_runnable_subqueries_beyond_the_cap_are_dropped():
    themes = [SemanticThemeIntent(text=f"t{i}") for i in range(MAX_SUBQUERIES + 2)]
    plan = compose([TrailSearchIntent(activity="mtb"), *themes])
    assert not plan.is_clarify
    # The trail search plus MAX_SUBQUERIES - 1 themes; the rest never run.
    assert plan.theme == "; ".join(f"t{i}" for i in range(MAX_SUBQUERIES - 1))


def test_zero_bounds_are_dropped_as_vacuous():
    """Strict mode makes the model emit every field; a 0 written where it means
    'no limit' must not silently filter out every trail."""
    plan = compose(
        [
            TrailSearchIntent(
                activity="mtb",
                max_distance_m=0.0,
                max_elevation_gain_m=0.0,
                min_distance_m=0.0,
                max_duration_min=120,
            )
        ]
    )
    assert plan.search is not None
    assert plan.search.max_distance_m is None
    assert plan.search.max_elevation_gain_m is None
    assert plan.search.min_distance_m is None
    assert plan.search.max_duration_min == 120  # real bounds survive


def test_all_zero_search_is_not_actionable():
    plan = compose([TrailSearchIntent(max_distance_m=0.0)])
    assert plan.is_clarify


def test_mixed_activity_is_dropped_as_no_preference():
    """The template already matches 'mixed' trails against every activity, so a
    "mixed" filter narrows to trails tagged both — the opposite of the "no
    preference" the model reaches for it to mean. Null searches everything."""
    plan = compose([TrailSearchIntent(activity="mixed", family_friendly=True)])
    assert plan.search is not None
    assert plan.search.activity is None
    assert plan.search.family_friendly is True  # real constraints survive


def test_named_activities_are_left_alone():
    for activity in ("mtb", "hike"):
        plan = compose([TrailSearchIntent(activity=activity, max_distance_m=20000)])
        assert plan.search is not None and plan.search.activity == activity


def test_mixed_only_search_is_not_actionable():
    """Dropping the sole filter leaves nothing to search on, so ask rather
    than return every trail we have."""
    assert compose([TrailSearchIntent(activity="mixed")]).is_clarify


def test_min_elevation_gain_merges_max_of_min():
    from chat.composer import merge_searches as merge

    merged = merge(
        [
            TrailSearchIntent(min_elevation_gain_m=500),
            TrailSearchIntent(min_elevation_gain_m=1000),
        ]
    )
    assert merged.min_elevation_gain_m == 1000


def test_has_constraints_sees_every_field():
    assert not has_constraints(TrailSearchIntent())
    assert has_constraints(TrailSearchIntent(season="summer"))
    assert has_constraints(TrailSearchIntent(family_friendly=True))
    assert has_constraints(TrailSearchIntent(surface_exclusions=["asphalt"]))


# ── Shared rules the orchestrator leans on ──────────────────────────────────


def test_the_family_cap_is_one_rule_both_paths_call():
    """One rule, one function: the orchestrator's difficulty cap for children
    is spelled once rather than re-derived by each caller."""
    from chat.composer import capped_difficulty

    assert capped_difficulty(3, True) == 1
    assert capped_difficulty(None, True) == 1  # unstated ceiling still caps
    assert capped_difficulty(3, False) == 3  # no flag, no cap
    assert capped_difficulty(None, False) is None


# ── An activity alone earns a guiding question, not an unbounded query ──────


def test_only_activity_sees_exactly_that():
    assert only_activity(TrailSearchIntent(activity="hike"))
    assert not only_activity(TrailSearchIntent())
    assert not only_activity(TrailSearchIntent(activity="hike", max_distance_m=9000))


def test_activity_alone_clarifies_with_shape_suggestions():
    """ "I want to hike" is in scope but unbounded — guide, then query. The
    suggestions must each be a complete ask that lands on a different shape
    of outing, so one tap answers the question and runs well."""
    plan = compose([TrailSearchIntent(activity="hike")])
    assert plan.is_clarify
    assert plan.clarify is not None
    assert len(plan.clarify.suggestions) >= 2
    assert any("loop" in s for s in plan.clarify.suggestions)


def test_activity_with_any_other_constraint_does_not_clarify():
    plan = compose([TrailSearchIntent(activity="hike", poi_types=["lake"])])
    assert not plan.is_clarify


def test_activity_beside_a_theme_or_outing_does_not_clarify():
    assert not compose(
        [TrailSearchIntent(activity="hike"), SemanticThemeIntent(text="shady forest")]
    ).is_clarify
    assert not compose(
        [
            TrailSearchIntent(activity="hike"),
            OutingIntent(
                activity="hike",
                start={"mode": "named", "name": "Lecco"},
                waypoints=[{"name": "Abbadia", "role": "end"}],
            ),
        ]
    ).is_clarify


def test_full_range_difficulty_is_vacuous_and_dropped():
    """min 1 / max 4 admits every trail — it is "any difficulty" written as
    numbers, which the model produces for bare invitations ("take me out on
    my bike"). It must not count as a constraint, or the guiding question
    for vague asks never fires."""
    plan = compose(
        [
            TrailSearchIntent(
                activity="mtb", min_difficulty_level=1, max_difficulty_level=4
            )
        ]
    )
    assert plan.is_clarify  # nothing real left beside the activity

    # A bound that actually bounds survives.
    plan = compose([TrailSearchIntent(activity="mtb", max_difficulty_level=2)])
    assert not plan.is_clarify
    assert plan.search is not None and plan.search.max_difficulty_level == 2


# ── The standing plan: apply_delta and its serde ─────────────────────────────
# Latest-wins on purpose. merge_searches is tightest-wins because two atoms in
# ONE message are one ask; a refinement turn is the user CHANGING the ask, so
# "actually, longer" must raise a max that tightest-wins would keep.


def _standing_outing(**overrides):
    from chat.intents import OutingIntent

    base = {"activity": "hike", "max_hours": 5.0, "max_distance_km": 15.0}
    return ComposedPlan(outing=OutingIntent(**{**base, **overrides}))


def test_delta_lowers_a_max():
    from chat.intents import OutingIntent

    merged = apply_delta(
        _standing_outing(),
        ComposedPlan(outing=OutingIntent(activity="hike", max_distance_km=10.0)),
    )
    assert merged.outing is not None
    assert merged.outing.max_distance_km == 10.0
    assert merged.outing.max_hours == 5.0  # untouched constraint survives


def test_delta_raises_a_max_where_tightest_wins_would_refuse():
    from chat.intents import OutingIntent

    merged = apply_delta(
        _standing_outing(),
        ComposedPlan(outing=OutingIntent(activity="hike", max_distance_km=20.0)),
    )
    assert merged.outing is not None
    assert merged.outing.max_distance_km == 20.0


def test_theme_delta_replaces_the_theme_and_keeps_the_search():
    standing = ComposedPlan(
        search=TrailSearchIntent(max_distance_m=12000.0), theme="panoramic ridge"
    )
    merged = apply_delta(standing, ComposedPlan(theme="shady forest"))
    assert merged.theme == "shady forest"
    assert merged.search is not None
    assert merged.search.max_distance_m == 12000.0


def test_a_standing_plan_from_before_routes_were_retired_still_loads():
    # jsonb written while RouteIntent existed carries a "routes" key; it is
    # ignored, and a row that held ONLY routes is no standing plan at all.
    loaded = standing_load(
        {
            "search": {"kind": "trail_search", "max_distance_m": 15000.0},
            "outing": None,
            "theme": None,
            "routes": [{"kind": "route", "start": "Lecco", "end": "Bergamo"}],
        }
    )
    assert loaded is not None
    assert loaded.search is not None and loaded.search.max_distance_m == 15000.0
    assert (
        standing_load(
            {
                "search": None,
                "outing": None,
                "theme": None,
                "routes": [{"kind": "route", "start": "a", "end": "b"}],
            }
        )
        is None
    )


def test_standing_dump_and_load_round_trip():
    from chat.intents import OutingIntent

    plan = ComposedPlan(
        outing=OutingIntent(activity="hike", max_distance_km=15.0),
        theme="lakeside",
    )
    loaded = standing_load(standing_dump(plan))
    assert loaded is not None
    assert loaded.outing == plan.outing
    assert loaded.theme == "lakeside"


def test_standing_dump_of_a_clarify_is_none():
    plan = ComposedPlan(clarify=ClarifyIntent(question="which one?"))
    assert standing_dump(plan) is None


def test_standing_load_degrades_malformed_history_to_none():
    assert standing_load(None) is None
    assert standing_load({"search": {"max_distance_m": "not a number"}}) is None
    assert standing_load({"search": None, "outing": None, "theme": None}) is None


# ── OutingIntent (Phase 12 R3) ───────────────────────────────────────────────


def _outing(**kwargs):
    from chat.intents import OutingIntent

    return OutingIntent(activity=kwargs.pop("activity", "bike"), **kwargs)


def test_outing_composes_verbatim():
    from chat.intents import StartSpec, Waypoint

    plan = compose(
        [
            _outing(
                party="kids",
                max_hours=3,
                waypoints=[Waypoint(kind="lake", role="pass")],
                start=StartSpec(mode="named", name="Lecco"),
            )
        ]
    )
    assert not plan.is_clarify
    assert plan.outing is not None and plan.outing.party == "kids"
    # The planner reads the ask itself; nothing is derived from it here.
    assert plan.outing.max_hours == 3
    assert plan.outing.waypoint_kinds == ["lake"]
    assert plan.outing.start.name == "Lecco"


def test_outing_with_clarify_is_poisoned():
    plan = compose(
        [_outing(), ClarifyIntent(question="who is asking?", suggestions=[])]
    )
    assert plan.is_clarify


def test_outing_survives_the_standing_roundtrip():
    plan = compose([_outing(party="kids", max_hours=3)])
    reloaded = standing_load(standing_dump(plan))
    assert reloaded is not None and reloaded.outing is not None
    assert reloaded.outing.party == "kids"
    assert reloaded.outing.max_hours == 3


def test_outing_delta_overlays_the_standing_ask():
    standing = compose([_outing(party="kids", max_hours=3)])
    delta = compose([_outing(max_hours=2)])
    merged = apply_delta(standing, delta)
    assert merged.outing.max_hours == 2
    assert merged.outing.party == "kids"  # unchanged constraint survives


# ── places the message never said (drop_unsaid_places) ──────────────────────


def test_a_region_the_message_never_named_is_dropped():
    """g32: after "... near Bergamo", "a bike route of less than 20 km" names
    no place — a carried region is the model's, not the walker's."""
    from chat.composer import drop_unsaid_places

    [s] = drop_unsaid_places(
        [TrailSearchIntent(activity="mtb", region="Bergamo")],
        "a bike route of less than 20 km",
    )
    assert s.region is None and s.activity == "mtb"


def test_carried_outing_places_are_dropped_said_ones_kept():
    from chat.composer import drop_unsaid_places

    carried = OutingIntent(
        activity="hike",
        shape="loop",
        area="Lecco",
        start={"mode": "named", "name": "Lecco"},
        waypoints=[
            {"name": "Abbadia", "role": "end"},
            {"kind": "lake", "name": "lake or river", "role": "bathe"},
        ],
    )
    [s] = drop_unsaid_places([carried], "a 10 km loop")
    assert s.area is None
    assert s.start.name is None and s.start.mode == "any"
    assert s.waypoint_names == []
    assert s.waypoint_kinds == ["lake"]  # the kind stays; only the name goes

    said = OutingIntent(
        activity="hike",
        start={"mode": "named", "name": "Lecco"},
        waypoints=[{"name": "Mandello del Lario", "role": "end"}],
    )
    [s] = drop_unsaid_places([said], "how do I get from lecco to Mandello?")
    assert s.start.name == "Lecco"
    assert s.waypoint_names == ["Mandello del Lario"]


def test_place_words_match_loosely_but_filler_does_not():
    from chat.composer import _said

    assert _said("the Orobie", "three days in the orobie")
    assert _said("Lecco station", "from lecco by train")
    assert not _said("Lago di Como", "a lake walk di sera")


def test_refining_to_a_loop_drops_the_standing_end():
    """g34: "how do I get from Lecco to Abbadia?" then "a 10 km loop" — a
    loop ends where it starts, so Abbadia cannot stay its end."""
    standing = ComposedPlan(
        outing=OutingIntent(
            activity="hike",
            start={"mode": "named", "name": "Lecco"},
            waypoints=[
                {"name": "Abbadia", "role": "end"},
                {"kind": "lake", "role": "pass"},
            ],
        )
    )
    merged = apply_delta(
        standing,
        ComposedPlan(
            outing=OutingIntent(activity="hike", shape="loop", max_distance_km=10)
        ),
    )
    assert merged.outing is not None
    assert merged.outing.waypoint_names == []
    assert merged.outing.waypoint_kinds == ["lake"]  # a pass-by stays


def test_a_new_end_on_a_standing_loop_makes_it_there_and_back():
    standing = ComposedPlan(
        outing=OutingIntent(activity="hike", shape="loop", area="Lecco")
    )
    merged = apply_delta(
        standing,
        ComposedPlan(
            outing=OutingIntent(
                activity="hike",
                start={"mode": "named", "name": "Abbadia"},
                waypoints=[
                    {"name": "Abbadia", "role": "end"},
                    {"name": "Mandello", "role": "end"},
                ],
            )
        ),
    )
    assert merged.outing is not None
    assert merged.outing.shape is None
    assert merged.outing.start.name == "Abbadia"
    assert merged.outing.waypoint_names == ["Mandello"]


def test_a_waypoint_repeating_the_start_never_reaches_the_plan():
    plan = compose(
        [
            OutingIntent(
                activity="hike",
                start={"mode": "named", "name": "Lecco"},
                waypoints=[
                    {"name": "lecco", "role": "pass"},
                    {"name": "Abbadia", "role": "end"},
                ],
            )
        ]
    )
    assert plan.outing is not None
    assert plan.outing.waypoint_names == ["Abbadia"]


def test_a_route_is_drawn_as_an_outing_never_walked_in_the_graph():
    plan = compose([RouteIntent(start="Ponteranica", end="Canto Alto")])
    assert plan.outing is not None
    assert plan.outing.start.mode == "named"
    assert plan.outing.start.name == "Ponteranica"
    assert plan.outing.waypoint_names == ["Canto Alto"]
    assert plan.outing.shape is None  # there and back, the planner's default


def test_a_route_with_one_end_missing():
    to = compose([RouteIntent(end="Canto Alto")]).outing
    assert to is not None and to.start.mode == "any"
    assert to.waypoint_names == ["Canto Alto"]
    frm = compose([RouteIntent(start="Bergamo", end="")]).outing
    assert frm is not None and frm.start.name == "Bergamo"
    assert frm.waypoints == [] and frm.shape == "loop"


def test_a_stated_outing_wins_over_a_route_in_the_same_turn():
    plan = compose(
        [
            OutingIntent(activity="mtb", shape="loop"),
            RouteIntent(start="Lecco", end="Abbadia"),
        ]
    )
    assert plan.outing is not None and plan.outing.activity == "mtb"
