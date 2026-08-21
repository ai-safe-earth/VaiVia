"""Composer: atomic subqueries merge deterministically, and thin plans clarify."""

from chat.composer import (
    MAX_ROUTES,
    MAX_SUBQUERIES,
    catalogue_view,
    compose,
    has_constraints,
    merge_searches,
    only_activity,
)
from chat.intents import (
    ClarifyIntent,
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


def test_subqueries_beyond_the_cap_are_dropped():
    subqueries = [
        TrailSearchIntent(activity="mtb"),
        SemanticThemeIntent(text="ridge"),
        RouteIntent(start="a", end="b"),
        RouteIntent(start="c", end="d"),
        ClarifyIntent(question="?"),  # fifth: beyond MAX_SUBQUERIES, ignored
    ]
    assert len(subqueries) == MAX_SUBQUERIES + 1
    plan = compose(subqueries)
    assert not plan.is_clarify


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
