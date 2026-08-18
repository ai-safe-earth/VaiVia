"""Duration estimation heuristics (minutes). Elevation-aware where data exists.

Hiking keeps the DIN 33466 *shape* — horizontal and vertical time computed
separately, total = max(component) + min(component) / 2 — with the vertical
rates calibrated up.

Why calibrated. DIN's 300 m/h ascent is deliberately conservative: it is a
safety-planning figure sized for the slowest reasonable party. Applied to a
catalogue it produced numbers nobody would believe. The reference case is the
classic Grigna ascent, 12 km and 1,600 m of climb, which guidebooks put at 6-8
hours; DIN gives 10.0, and the 20 km catalogue loops came out over 15 hours. A
figure a walker knows is wrong is worse than no figure, because it makes every
other number on the card suspect.

At 450 m/h up and 600 m/h down the reference lands at 7.7 hours, inside the
guidebook band, and flat walking is untouched at 4 km/h — the error was always
in the vertical term. The pace this assumes is a moving one for an averagely
fit walker with no long stops, which is the same thing guidebook times assume,
and it is a PRODUCT decision rather than a derived constant: moving these three
numbers moves every duration in the app. `test_durations.py` pins the reference
case so a change to them has to be deliberate.

MTB is a pragmatic heuristic: base speed by difficulty level, plus a climbing
penalty of one hour per 800 m of ascent. Documented, not gospel — recalibrate
against real ride data when we have it.
"""

# See the module docstring: a product decision, not a derived constant.
HIKE_FLAT_KMH = 4.0
HIKE_ASCENT_MH = 450.0
HIKE_DESCENT_MH = 600.0

MTB_SPEED_KMH_BY_LEVEL = {1: 15.0, 2: 13.0, 3: 10.0, 4: 8.0}

DIFFICULTY_LEVELS = {"Easy": 1, "Intermediate": 2, "Difficult": 3, "Pro": 4}


def difficulty_level(label: str) -> int:
    try:
        return DIFFICULTY_LEVELS[label]
    except KeyError:
        raise ValueError(f"Unknown difficulty label: {label!r}") from None


def hike_duration_min(
    distance_m: float, elevation_gain_m: float | None, elevation_loss_m: float | None
) -> int:
    horizontal_h = (distance_m / 1000) / HIKE_FLAT_KMH
    vertical_h = (elevation_gain_m or 0.0) / HIKE_ASCENT_MH + (
        elevation_loss_m or 0.0
    ) / HIKE_DESCENT_MH
    total_h = max(horizontal_h, vertical_h) + min(horizontal_h, vertical_h) / 2
    return round(total_h * 60)


def mtb_duration_min(
    distance_m: float, elevation_gain_m: float | None, level: int
) -> int:
    speed = MTB_SPEED_KMH_BY_LEVEL.get(level, MTB_SPEED_KMH_BY_LEVEL[2])
    riding_h = (distance_m / 1000) / speed
    climbing_h = (elevation_gain_m or 0.0) / 800.0
    return round((riding_h + climbing_h) * 60)
