"""Composer: atomic subqueries merge deterministically, and thin plans clarify."""

from chat.composer import (
    MAX_ROUTES,
    MAX_SUBQUERIES,
    compose,
    has_constraints,
    merge_searches,
)
from chat.intents import (
    ClarifyIntent,
    RouteIntent,
    SemanticThemeIntent,
    TrailSearchIntent,
)


def test_single_search_passes_through():
    plan = compose([TrailSearchIntent(activity="mtb")])
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
        plan = compose([TrailSearchIntent(activity=activity)])
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
