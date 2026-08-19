"""The legality rules are the new hard exclusion; every branch is pinned."""

from load.legality import routable_bike, routable_foot


def test_plain_path_is_walkable_and_bikeable() -> None:
    tags = {"highway": "path"}
    assert routable_foot(tags) == (True, None)
    assert routable_bike(tags) == (True, None)


def test_private_track_is_excluded() -> None:
    # The old ingestion routed over these; that is the bug this module fixes.
    tags = {"highway": "track", "access": "private"}
    assert routable_foot(tags) == (False, "access=private")
    assert routable_bike(tags) == (False, "access=private")


def test_specific_permission_overrides_general_refusal() -> None:
    # A signed-through path on private land: common in these valleys.
    tags = {"highway": "path", "access": "private", "foot": "yes"}
    assert routable_foot(tags) == (True, None)
    # ...but the permission is per mode: the bike is still refused.
    assert routable_bike(tags) == (False, "access=private")


def test_foot_no_excludes_walkers_only() -> None:
    tags = {"highway": "cycleway", "foot": "no", "bicycle": "designated"}
    assert routable_foot(tags) == (False, "foot=no")
    assert routable_bike(tags) == (True, None)


def test_steps_are_impassable_on_a_bike_not_merely_slow() -> None:
    tags = {"highway": "steps"}
    assert routable_foot(tags) == (True, None)
    assert routable_bike(tags) == (False, "highway=steps")


def test_missing_tags_permit() -> None:
    # Most legal paths carry no access tag; absence must not erase the network.
    assert routable_foot({"highway": "track"}) == (True, None)
    assert routable_bike({"highway": "track"}) == (True, None)


def test_non_walkable_highway_is_out_for_both() -> None:
    for highway in ("motorway", "trunk", "primary", None):
        tags = {"highway": highway} if highway else {}
        ok, reason = routable_foot(tags)
        assert not ok and reason == f"highway={highway}"
        ok, reason = routable_bike(tags)
        assert not ok


def test_destination_permits() -> None:
    # A walker is always destination traffic.
    tags = {"highway": "service", "access": "destination"}
    assert routable_foot(tags) == (True, None)
