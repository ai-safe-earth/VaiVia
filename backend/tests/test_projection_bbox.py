"""A GDS projection bbox is the query's or the graph's, never the app's.

This bug has now been fixed three times — `api/routes/routing.py`,
`scripts/build_trailheads.py`, and `scripts/check_graph_connectivity.py` plus
`scripts/build_routes.py` — and each time it was invisible: the projection
succeeds, the queries succeed, and the answer is simply computed over 37% of the
network. Nothing counts wrong; the count is just of the wrong graph. That is
exactly the shape a guard test exists for (docs/fragilities.md #16).

The bbox-parsing half is pure, so it is tested directly rather than against a
database — the same split `pipeline/curate/anchors.py` uses.
"""

import ast
from pathlib import Path

import pytest

from graph.extent import describe, parse_bbox, projection_bbox

SOURCE_ROOT = Path(__file__).resolve().parents[1]

#: Every file that builds a GDS projection. Discovered rather than listed, so a
#: new projecting script is covered the day it is written instead of the day
#: someone remembers this file.
PROJECTION_MARKER = "graph_project_routing"

#: Reading either of these off the settings object is the bug.
FORBIDDEN_ATTRIBUTES = {"bbox", "default_bbox"}

#: How the settings object is spelled. The fifth copy of this bug will not be
#: written in the spelling the fourth one was, so the guard has to recognise all
#: three reachings for it: a module-level `settings`, a local bound from the
#: factory, and the factory called inline — which is the form
#: tests/test_api_routing.py already uses.
SETTINGS_FACTORY = "get_settings"


def _projecting_sources() -> list[Path]:
    found = [
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if ".venv" not in path.parts
        and path.parent.name != "tests"
        and PROJECTION_MARKER in path.read_text(encoding="utf-8")
    ]
    assert found, "no projecting source found — has the marker been renamed?"
    return found


def _is_settings_call(node: ast.AST) -> bool:
    """Is this `get_settings()` / `config.get_settings()`?"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == SETTINGS_FACTORY
    return isinstance(func, ast.Attribute) and func.attr == SETTINGS_FACTORY


def _settings_receivers(tree: ast.AST) -> set[str]:
    """Names holding the settings object: `settings`, plus every local assigned
    from the factory (`cfg = get_settings()`)."""
    names = {"settings"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_settings_call(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_settings_call(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _settings_attributes_read(source: str) -> set[str]:
    """Which settings attributes the source actually reads.

    Parsed, not grepped. Every one of these files explains in prose why it no
    longer uses `settings.bbox`, and that record is the most valuable line in
    them — a text search would force the comment to be deleted to make the test
    pass, which is precisely backwards.

    `default_bbox` counts on ANY receiver: nothing else in the codebase carries
    that name, so `whatever.default_bbox` is the settings object under an alias
    the two rules above did not catch. `bbox` deliberately does not get that
    treatment — `args.bbox` is the argparse option, and flagging it would break
    the very scripts that took the option instead of the setting.
    """
    tree = ast.parse(source)
    receivers = _settings_receivers(tree)
    read: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if (
            (isinstance(node.value, ast.Name) and node.value.id in receivers)
            or _is_settings_call(node.value)
            or node.attr == "default_bbox"
        ):
            read.add(node.attr)
    return read


def test_no_projection_reads_the_configured_bbox():
    for path in _projecting_sources():
        read = _settings_attributes_read(path.read_text(encoding="utf-8"))
        offending = read & FORBIDDEN_ATTRIBUTES
        assert not offending, (
            f"{path.relative_to(SOURCE_ROOT)} projects GDS from "
            f"settings.{offending.pop()}; that is the ingestion bound. Use "
            "graph.extent.projection_bbox (docs/fragilities.md #16)"
        )


@pytest.mark.parametrize(
    "source",
    [
        "min_lat, min_lon, max_lat, max_lon = settings.bbox",
        # The form tests/test_api_routing.py already uses — and the one the old
        # guard, which only matched an ast.Name called `settings`, walked past.
        "min_lat, min_lon, max_lat, max_lon = get_settings().bbox",
        "min_lat, min_lon, max_lat, max_lon = config.get_settings().bbox",
        "cfg = get_settings()\nmin_lat, min_lon, max_lat, max_lon = cfg.bbox",
        "cfg: Settings = get_settings()\nlo = cfg.bbox",
    ],
)
def test_the_guard_would_catch_the_bug_it_exists_for(source):
    """A guard nobody has seen fail is a guard nobody knows works."""
    assert "bbox" in _settings_attributes_read(source)


def test_default_bbox_is_caught_whatever_it_is_read_off():
    """No other object in the codebase has this attribute, so any receiver at
    all is the settings object wearing a name the guard does not know."""
    assert _settings_attributes_read("lo = whatever.default_bbox") == {"default_bbox"}


def test_the_guard_leaves_the_cli_option_alone():
    """`--bbox` is the fix, not the bug: flagging `args.bbox` would fail exactly
    the scripts that stopped reading the setting."""
    assert _settings_attributes_read("lo = args.bbox") == set()


def test_the_guard_ignores_prose():
    assert _settings_attributes_read("# settings.bbox in a comment") == set()
    assert _settings_attributes_read('"""settings.bbox in a docstring"""') == set()


def test_parse_bbox_reads_lat_lon_order():
    assert parse_bbox("45.8,9.3,46.0,9.6") == (45.8, 9.3, 46.0, 9.6)
    assert parse_bbox(" 45.8 , 9.3 , 46.0 , 9.6 ") == (45.8, 9.3, 46.0, 9.6)


@pytest.mark.parametrize(
    "text",
    [
        "45.8,9.3,46.0",  # three
        "45.8,9.3,46.0,9.6,1",  # five
        "45.8,9.3,north,9.6",  # not a float
        "46.0,9.3,45.8,9.6",  # min_lat above max_lat
        "45.8,9.6,46.0,9.3",  # min_lon east of max_lon
        # float() takes these, and every min < max comparison is False for NaN,
        # so without an explicit check they reach the projection and it comes
        # back empty — reading as "nothing is ingested", not "your argument".
        "nan,nan,nan,nan",
        "45.8,9.3,inf,9.6",
        "-inf,9.3,46.0,9.6",
    ],
)
def test_a_malformed_bbox_raises_rather_than_falling_back(text):
    """Silently substituting a default is how a run covers ground nobody chose."""
    with pytest.raises(ValueError):
        parse_bbox(text)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[str] = []

    async def run_named(self, name, /, **params):
        self.calls.append(name)
        return self.rows


@pytest.mark.asyncio
async def test_projection_bbox_defaults_to_the_graphs_own_extent():
    db = _FakeDb([{"min_lat": 45.6, "min_lon": 9.2, "max_lat": 46.1, "max_lon": 9.9}])
    assert await projection_bbox(db) == (45.6, 9.2, 46.1, 9.9)
    assert db.calls == ["graph_extent"]


@pytest.mark.asyncio
async def test_an_explicit_bbox_does_not_touch_the_database():
    db = _FakeDb([])
    assert await projection_bbox(db, "45.8,9.3,46.0,9.6") == (45.8, 9.3, 46.0, 9.6)
    assert db.calls == []


@pytest.mark.asyncio
async def test_an_empty_graph_fails_loudly():
    """Absent is not zero: a (0,0,0,0) box would project nothing and read as a
    healthy empty answer, which is the failure mode this whole file is about."""
    db = _FakeDb([{"min_lat": None, "min_lon": None, "max_lat": None, "max_lon": None}])
    with pytest.raises(RuntimeError, match="no located intersections"):
        await projection_bbox(db)


def test_describe_names_where_the_box_came_from():
    bbox = (45.8, 9.3, 46.0, 9.6)
    assert "the whole ingested graph" in describe(bbox)
    assert "--bbox" in describe(bbox, "45.8,9.3,46.0,9.6")
