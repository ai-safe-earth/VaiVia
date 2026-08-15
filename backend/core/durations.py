"""Duration estimation heuristics (minutes). Elevation-aware where data exists.

Hiking follows DIN 33466: horizontal time at 4 km/h, vertical time at 300 m/h
ascent and 500 m/h descent; total = max(component) + min(component) / 2.

MTB is a pragmatic heuristic: base speed by difficulty level, plus a climbing
penalty of one hour per 800 m of ascent. Documented, not gospel — recalibrate
against real ride data when we have it.
"""

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
    horizontal_h = (distance_m / 1000) / 4.0
    vertical_h = (elevation_gain_m or 0.0) / 300.0 + (elevation_loss_m or 0.0) / 500.0
    total_h = max(horizontal_h, vertical_h) + min(horizontal_h, vertical_h) / 2
    return round(total_h * 60)


def mtb_duration_min(
    distance_m: float, elevation_gain_m: float | None, level: int
) -> int:
    speed = MTB_SPEED_KMH_BY_LEVEL.get(level, MTB_SPEED_KMH_BY_LEVEL[2])
    riding_h = (distance_m / 1000) / speed
    climbing_h = (elevation_gain_m or 0.0) / 800.0
    return round((riding_h + climbing_h) * 60)
