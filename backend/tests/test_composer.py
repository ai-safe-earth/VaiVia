"""Composer: atomic subqueries merge deterministically, and thin plans clarify."""

from chat.composer import (
    MAX_ROUTES,
    MAX_SUBQUERIES,
    ComposedPlan,
    apply_delta,
    catalogue_view,
    compose,
    has_constraints,
    merge_searches,
    only_activity,
    standing_dump,
    standing_load,
)
from chat.intents import (
    ClarifyIntent,
    LoopSearchIntent,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)


def test_single_search_passes_through():
    plan = compose([TrailSearchIntent(activity="mtb", max_distance_m=20000)])
    assert not plan.is_clarify
    assert plan.search is not None and plan.search.activity == "mtb"
    assert plan.theme is None
    assert plan.routes == []


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
    assert plan.search is None and plan.routes == []


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


def test_routes_are_kept_in_order_and_capped():
    routes = [RouteIntent(start=f"a{i}", end=f"b{i}") for i in range(4)]
    plan = compose(list(routes))
    assert [r.start for r in plan.routes] == ["a0", "a1"][:MAX_ROUTES]


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
        RouteIntent(start="a", end="b"),
        RouteIntent(start="c", end="d"),
        ClarifyIntent(question="?"),  # fifth: past MAX_SUBQUERIES, still heard
    ]
    assert len(subqueries) == MAX_SUBQUERIES + 1
    plan = compose(subqueries)
    assert plan.is_clarify
    assert plan.search is None and not plan.routes and plan.theme is None


def test_runnable_subqueries_beyond_the_cap_are_dropped():
    routes = [
        RouteIntent(start=f"a{i}", end=f"b{i}") for i in range(MAX_SUBQUERIES + 2)
    ]
    plan = compose([TrailSearchIntent(activity="mtb"), *routes])
    assert not plan.is_clarify
    # The trail search plus MAX_SUBQUERIES - 1 routes, themselves capped again.
    assert len(plan.routes) <= MAX_ROUTES


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
    than return the whole catalogue."""
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


# ── A trail ask is posed to the catalogue too — when it can be ──────────────


def test_catalogue_view_maps_the_shared_constraints():
    view = catalogue_view(
        TrailSearchIntent(
            activity="hike",
            min_distance_m=8000,
            max_distance_m=16000,
            max_difficulty_level=3,
            max_elevation_gain_m=1200,
            poi_types=["peak"],
            region="Bergamo",
        )
    )
    assert view is not None
    assert view.activity == "hike"
    assert view.min_distance_m == 8000
    assert view.max_distance_m == 16000
    assert view.max_difficulty_level == 3
    assert view.max_ascent_m == 1200
    assert view.poi_types == ["peak"]
    assert view.near == "Bergamo"


def test_catalogue_view_refuses_what_the_catalogue_cannot_honour():
    """A constraint the catalogue cannot express must kill the view, not be
    dropped: routes that silently ignore a stated season or hazard would be
    a lie shaped like a result."""
    cases = [
        TrailSearchIntent(activity="hike", season="winter"),
        TrailSearchIntent(activity="hike", exclude_hazards=["ice"]),
        TrailSearchIntent(activity="hike", surface_exclusions=["asphalt"]),
        # The template carries ceilings only.
        TrailSearchIntent(activity="hike", min_difficulty_level=2),
        TrailSearchIntent(activity="hike", min_elevation_gain_m=500),
    ]
    for search in cases:
        assert catalogue_view(search) is None, search


def test_catalogue_view_drops_duration_like_the_explicit_loop_path():
    """The ratified duration rule (2026-08-21): the catalogue carries no
    duration until DIN 33466 is calibrated, and dropping the filter loudly
    beats refusing to answer -- the explicit loop path already drops it, so
    the derived view must too, or "a two hour hike" never sees the
    catalogue at all."""
    view = catalogue_view(TrailSearchIntent(activity="hike", max_duration_min=120))
    assert view is not None
    assert view.max_duration_min is None


def test_catalogue_view_family_friendly_caps_the_ceiling_at_one():
    view = catalogue_view(TrailSearchIntent(family_friendly=True, max_distance_m=5000))
    assert view is not None and view.max_difficulty_level == 1
    view = catalogue_view(
        TrailSearchIntent(family_friendly=True, max_difficulty_level=3)
    )
    assert view is not None and view.max_difficulty_level == 1


def test_the_family_cap_is_one_rule_both_paths_call():
    """The trail search and the catalogue view both promise the same thing
    about children, so they ask the same function rather than each spelling
    the arithmetic out."""
    from chat.composer import capped_difficulty

    assert capped_difficulty(3, True) == 1
    assert capped_difficulty(None, True) == 1  # unstated ceiling still caps
    assert capped_difficulty(3, False) == 3  # no flag, no cap
    assert capped_difficulty(None, False) is None


def test_catalogue_view_mixed_reaches_the_catalogue_as_no_preference():
    view = catalogue_view(TrailSearchIntent(activity="mixed", max_distance_m=9000))
    assert view is not None and view.activity is None


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


def test_activity_beside_a_theme_or_route_does_not_clarify():
    assert not compose(
        [TrailSearchIntent(activity="hike"), SemanticThemeIntent(text="shady forest")]
    ).is_clarify
    assert not compose(
        [
            TrailSearchIntent(activity="hike"),
            RouteIntent(start="Lecco", end="Abbadia"),
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


def _standing_loop(**overrides):
    base = {"activity": "hike", "min_distance_m": 8000.0, "max_distance_m": 15000.0}
    return ComposedPlan(loop=LoopSearchIntent(**{**base, **overrides}))


def test_delta_lowers_a_max():
    merged = apply_delta(
        _standing_loop(), ComposedPlan(loop=LoopSearchIntent(max_distance_m=10000.0))
    )
    assert merged.loop is not None
    assert merged.loop.max_distance_m == 10000.0
    assert merged.loop.min_distance_m == 8000.0  # untouched constraint survives
    assert merged.loop.activity == "hike"


def test_delta_raises_a_max_where_tightest_wins_would_refuse():
    merged = apply_delta(
        _standing_loop(max_distance_m=10000.0),
        ComposedPlan(loop=LoopSearchIntent(max_distance_m=20000.0)),
    )
    assert merged.loop is not None
    assert merged.loop.max_distance_m == 20000.0


def test_delta_adds_a_poi_without_disturbing_the_rest():
    merged = apply_delta(
        _standing_loop(), ComposedPlan(loop=LoopSearchIntent(poi_types=["lake"]))
    )
    assert merged.loop is not None
    assert merged.loop.poi_types == ["lake"]
    assert merged.loop.max_distance_m == 15000.0


def test_cross_kind_delta_lands_on_the_standing_loop():
    # "shorter" against a loop often arrives as a trail_search; the shared
    # constraint carries over and the plan STAYS a loop plan, so the same
    # catalogue answers.
    merged = apply_delta(
        _standing_loop(),
        ComposedPlan(search=TrailSearchIntent(max_distance_m=10000.0)),
    )
    assert merged.search is None
    assert merged.loop is not None
    assert merged.loop.max_distance_m == 10000.0
    assert merged.loop.activity == "hike"


def test_cross_kind_delta_never_moves_activity():
    # The two intents spell activity differently; a cross-carry could flip
    # which catalogue is searched.
    merged = apply_delta(
        _standing_loop(),
        ComposedPlan(search=TrailSearchIntent(activity="mtb", max_distance_m=9000.0)),
    )
    assert merged.loop is not None
    assert merged.loop.activity == "hike"
    assert merged.loop.max_distance_m == 9000.0


def test_theme_delta_replaces_the_theme_and_keeps_the_search():
    standing = ComposedPlan(
        search=TrailSearchIntent(max_distance_m=12000.0), theme="panoramic ridge"
    )
    merged = apply_delta(standing, ComposedPlan(theme="shady forest"))
    assert merged.theme == "shady forest"
    assert merged.search is not None
    assert merged.search.max_distance_m == 12000.0


def test_standing_routes_are_never_carried_into_a_refinement():
    # A route is a one-shot answer, not a constraint in force: carrying it
    # re-ran "Lecco to Bergamo" under every later ask (owner session,
    # 2026-08-26, via dump_conversation).
    standing = ComposedPlan(
        search=TrailSearchIntent(max_distance_m=15000.0),
        routes=[RouteIntent(start="Lecco", end="Bergamo")],
    )
    merged = apply_delta(
        standing, ComposedPlan(search=TrailSearchIntent(max_distance_m=10000.0))
    )
    assert merged.routes == []
    assert merged.search is not None
    assert merged.search.max_distance_m == 10000.0


def test_a_route_only_delta_is_a_change_of_subject():
    merged = apply_delta(
        _standing_loop(),
        ComposedPlan(routes=[RouteIntent(start="Abbadia", end="Lecco")]),
    )
    assert merged.loop is None
    assert len(merged.routes) == 1


def test_standing_dump_and_load_round_trip():
    plan = ComposedPlan(
        loop=LoopSearchIntent(activity="hike", max_distance_m=15000.0),
        theme="lakeside",
    )
    loaded = standing_load(standing_dump(plan))
    assert loaded is not None
    assert loaded.loop == plan.loop
    assert loaded.theme == "lakeside"


def test_standing_dump_of_a_clarify_is_none():
    plan = ComposedPlan(clarify=ClarifyIntent(question="which one?"))
    assert standing_dump(plan) is None


def test_standing_load_degrades_malformed_history_to_none():
    assert standing_load(None) is None
    assert standing_load({"search": {"max_distance_m": "not a number"}}) is None
    assert standing_load({"search": None, "loop": None, "theme": None}) is None


# ── OutingIntent (Phase 12 R3) ───────────────────────────────────────────────


def _outing(**kwargs):
    from chat.intents import OutingIntent

    return OutingIntent(activity=kwargs.pop("activity", "bike"), **kwargs)


def test_outing_composes_with_an_interim_catalogue_view():
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
    # the degradation until R4: the same ask posed to the catalogue
    assert plan.loop is not None
    assert plan.loop.activity == "mtb"
    assert plan.loop.max_duration_min == 180
    assert plan.loop.max_difficulty_level == 1
    assert plan.loop.poi_types == ["lake"]
    assert plan.loop.near == "Lecco"


def test_multi_day_outing_has_no_one_day_stand_in():
    plan = compose([_outing(days=3, sleep="hut")])
    assert plan.outing is not None and plan.loop is None


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


def test_outing_delta_overlays_and_refreshes_the_view():
    standing = compose([_outing(party="kids", max_hours=3)])
    delta = compose([_outing(max_hours=2)])
    merged = apply_delta(standing, delta)
    assert merged.outing.max_hours == 2
    assert merged.outing.party == "kids"  # unchanged constraint survives
    assert merged.loop is not None and merged.loop.max_duration_min == 120
