"""The composed plan, in the words a walker used to ask for it.

"How I read it" is load-bearing in the brand spec: it is what lets someone
correct one constraint instead of rewriting the whole question. It sat inert
since the brand pass because `/chat` streamed results and never the plan
behind them.

What it must show is not the model's subqueries but **what was actually
executed** — which is a different thing, and the interesting one, because the
composer makes decisions of its own:

  * "family friendly" CAPS difficulty at 1 whatever else was said;
  * features are a CONJUNCTION — "a lake or a peak" runs as lake AND peak,
    which a walker cannot otherwise tell from the answer.

This describes the TRAIL side only. A drawn outing reads itself back through
the planner's assumptions strip ("reading ~3 h as 12-18 km"), which is a
statement about the numbers compile.py chose and belongs with them.

Pure, so every one of those is pinned by a test. Presentation vocabulary lives
here rather than in the frontend because these are statements about what the
backend did; a reader that re-derived them could drift from the truth.
"""

from __future__ import annotations

from typing import Any

from chat.composer import ComposedPlan
from chat.intents import TrailSearchIntent

#: Our 1-4 scale in the words the intent prompt uses for it.
DIFFICULTY_WORDS = {1: "easy", 2: "intermediate", 3: "difficult", 4: "hardest"}

ACTIVITY_WORDS = {"hike": "on foot", "mtb": "by mountain bike"}


def _km(metres: float) -> str:
    """Metres as a walker says them. Whole kilometres above 10 km."""
    km = metres / 1000
    return f"{km:.0f} km" if km >= 10 else f"{km:.1f} km"


def _hours(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" if rest == 0 else f"{hours} h {rest} min"


def _band(low: float | None, high: float | None) -> str | None:
    if low is not None and high is not None:
        # A band of one number is an equality filter, and "15 km to 15 km"
        # reads like a range that happens to be narrow. Say what it does.
        if low == high:
            return f"exactly {_km(low)}"
        return f"{_km(low)} to {_km(high)}"
    if high is not None:
        return f"under {_km(high)}"
    if low is not None:
        return f"over {_km(low)}"
    return None


def _climb_band(low: float | None, high: float | None) -> str | None:
    """Climb is metres of ascent, never kilometres of anything."""
    if low is not None and high is not None:
        return f"{round(low)} to {round(high)} m"
    if high is not None:
        return f"under {round(high)} m"
    if low is not None:
        return f"over {round(low)} m"
    return None


def _row(rows: list[dict[str, str]], key: str, value: str | None) -> None:
    if value:
        rows.append({"key": key, "value": value})


def _features(poi_types: list[str]) -> str | None:
    """Features as the query runs them: every one must be present.

    The AND is spelled out because the template is a conjunction and nothing
    else on screen reveals it — a walker who asked for "a lake or a hut" and
    got three results has no way to know both were required.
    """
    named = [p.replace("_", " ") for p in poi_types]
    if not named:
        return None
    if len(named) == 1:
        return named[0]
    return " and ".join([", ".join(named[:-1]), named[-1]])


def _search_rows(search: TrailSearchIntent, rows: list[dict[str, str]]) -> None:
    """The trail ask as it ran."""
    _row(rows, "activity", ACTIVITY_WORDS.get(search.activity or ""))
    _row(rows, "distance", _band(search.min_distance_m, search.max_distance_m))
    _row(
        rows,
        "climb",
        _climb_band(search.min_elevation_gain_m, search.max_elevation_gain_m),
    )
    if search.max_duration_min is not None:
        # Trails ARE post-filtered by duration, so this one is a filter that
        # really ran and can be stated plainly.
        _row(rows, "time", f"under {_hours(search.max_duration_min)}")
    if search.family_friendly:
        # The cap is applied in the orchestrator, so say the cap, not the flag.
        _row(rows, "difficulty", "easy only, for children")
    else:
        _row(
            rows,
            "difficulty",
            _difficulty(search.min_difficulty_level, search.max_difficulty_level),
        )
    _row(rows, "passes", _features(list(search.poi_types)))
    _row(rows, "near", search.region)
    # No mapping: a season is already the word a walker said.
    _row(rows, "season", search.season)
    _row(
        rows, "avoiding", ", ".join(h.replace("_", " ") for h in search.exclude_hazards)
    )
    _row(
        rows,
        "not on",
        ", ".join(s.replace("_", " ") for s in search.surface_exclusions),
    )


def _difficulty(low: int | None, high: int | None) -> str | None:
    if low is not None and high is not None and low == high:
        return DIFFICULTY_WORDS.get(high)
    if low is not None and high is not None:
        # Both ran, so both are shown. Rendering the ceiling alone hid a floor
        # the query applied, and a hidden filter is the one thing a readback
        # must never do.
        return f"{DIFFICULTY_WORDS.get(low, low)} to {DIFFICULTY_WORDS.get(high, high)}"
    if high is not None:
        return f"{DIFFICULTY_WORDS.get(high, high)} at most"
    if low is not None:
        return f"{DIFFICULTY_WORDS.get(low, low)} at least"
    return None


def describe(plan: ComposedPlan) -> list[dict[str, str]]:
    """The plan as rows of (key, value). Empty when nothing was searched."""
    if plan.is_clarify:
        return []

    rows: list[dict[str, str]] = []

    if plan.search is not None:
        _search_rows(plan.search, rows)
        if plan.theme is not None:
            _row(rows, "looked in", "named trails, matched by description")
        else:
            _row(rows, "looked in", "named trails")
    elif plan.theme is not None:
        _row(rows, "looked in", "named trails, matched by description")

    if plan.theme is not None:
        _row(rows, "described as", plan.theme)

    for route in plan.routes:
        _row(rows, "route", f"{route.start} to {route.end}")

    return rows


def readback(plan: ComposedPlan) -> dict[str, Any]:
    """The results-event fragment: absent when there is nothing to read back."""
    rows = describe(plan)
    return {"reading": rows} if rows else {}
