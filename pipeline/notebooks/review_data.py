"""What the state notebook reads and draws. The notebook holds the narrative.

Splitting it this way keeps the committed .ipynb small enough to diff, keeps the
parts worth testing in a file the pure-function suite can reach, and keeps every
cell to one line that says what it wants rather than how to get it.

TWO RULES HOLD THIS TOGETHER.

The metric definitions are IMPORTED, never restated. `export.review_bundle`
already owns "how many starts are on an island", and retyping its twenty-three
one-row queries here would create a second answer to that question -- the drift
the shared qa.v_* views and the shared fragment mechanism exist to prevent. If a
number is wrong it should be wrong in one place.

And colour comes from the `*_class` / `*_band` twins the views already carry.
They are numbered so they sort (`1 flat (<5%)`), which is why QGIS can colour by
Categorized without an expression -- and it means this notebook and QGIS colour
the same thing the same way instead of drifting apart on palettes.

Run from `pipeline/`: this is a non-packaged uv project with flat modules, so
`from core import ...` resolves from there and nowhere else.
"""

from __future__ import annotations

import io
import textwrap

import contextily as cx
import geopandas as gpd
import matplotlib
import pandas as pd

matplotlib.use("Agg")  # no display in this environment
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from shapely.geometry import LineString

from core import CRS_METRIC, CRS_STORAGE, REGIONS, connect, sqlalchemy_url
from export.review_bundle import ISSUES, SETTLED, STATE

# topology/histogram.py's palette, so every figure the pipeline produces looks
# like it came from the same place.
INK = "#333333"
GREY = "#888888"
FAINT = "#dddddd"
BLUE = "#4a6fa5"
RED = "#a5484a"
GREEN = "#4a8a5c"
SAND = "#c8a45c"

#: Ordered, for a class column whose categories run from good to bad or low to
#: high. Index 0 is the `0 ...` / `1 ...` category, and so on down.
SEQUENCE = ["#3f6fb0", "#6f9fd0", "#9fc4e0", "#e8c35a", "#d98b45", "#b5453f"]

DPI = 130

#: Tiles are in Web Mercator, so anything drawn over them has to be too. The
#: store is 4326 (core.CRS_STORAGE) and measurement happens in UTM
#: (core.CRS_METRIC); this is a third CRS that exists only for drawing.
CRS_TILES = 3857

#: A reference map to place things, and an earth image to see the ground they
#: are on. Positron is deliberately pale: the data is the subject, and a
#: basemap that competes with it is worse than none.
#:
#: Tiles are fetched when the notebook EXECUTES and rasterised into the PNGs,
#: so the exported page still opens with no network. Only a refresh needs one.
REFERENCE_TILES = cx.providers.CartoDB.Positron
IMAGERY_TILES = cx.providers.Esri.WorldImagery


#: No tile provider goes deeper than this, and asking for more returns a
#: blurred upscale plus a warning in the middle of the page.
MAX_TILE_ZOOM = 19


def _basemap(ax, source=REFERENCE_TILES, zoom="auto") -> None:
    """Put tiles under the axes, keeping their attribution on the picture.

    The attribution is not decoration: these tiles are somebody's work under a
    licence, and this project already carries OSM's credit into the app chrome
    for the same reason.
    """
    if zoom == "auto":
        span = abs(ax.get_xlim()[1] - ax.get_xlim()[0])
        if span and span < 400:  # metres: deeper than any provider serves
            zoom = MAX_TILE_ZOOM
    try:
        cx.add_basemap(
            ax, source=source, crs=CRS_TILES, zoom=zoom, attribution_size=5
        )
    except Exception as error:  # noqa: BLE001 - a map without tiles still reads
        ax.set_facecolor("#f7f7f7")
        print(f"  no basemap ({type(error).__name__}: {error})")


def _tiles(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The same features, in the CRS the tiles are drawn in."""
    return frame.to_crs(CRS_TILES)


def _url() -> str:
    """The SQLAlchemy URL, which is not the same string as the psycopg one.

    A bare postgresql:// resolves to psycopg2, which this project deliberately
    does not install; geopandas builds an engine internally, so anything going
    through read_postgis needs the +psycopg form.
    """
    return sqlalchemy_url()


def frame(sql: str, **params) -> pd.DataFrame:
    """A query as a plain frame, run the way the bundle runs it.

    Through psycopg directly rather than pandas' SQLAlchemy path, for a reason
    that bit once: one of the imported metric queries filters on the literal
    `'3 fragment (<20%)'`, and a driver that scans for placeholders reads that
    `%)` as one. The bundle executes these on a plain connection, so executing
    them the same way is both what makes the numbers identical and what stops
    a category label from being parsed as syntax.
    """
    with connect() as conn:
        cursor = conn.execute(sql, params or None)
        columns = [column.name for column in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


def geoframe(sql: str, **params) -> gpd.GeoDataFrame:
    """A query as a GeoDataFrame. The geometry column is always `geom`."""
    return gpd.read_postgis(sql, _url(), geom_col="geom", params=params or None)


# --------------------------------------------------------------------------
# the numbers
# --------------------------------------------------------------------------

_METRICS = {"state": STATE, "settled": SETTLED, "issues": ISSUES}


def metrics(kind: str) -> pd.DataFrame:
    """One of the three metric lists, run against the live store.

    `kind` is 'state', 'settled' or 'issues'. The lists come from
    export.review_bundle, so these are the same numbers the generated
    review/README.md reports, by construction rather than by agreement.
    """
    rows = [
        {"label": label, "value": frame(sql).iloc[0, 0], "note": note}
        for label, sql, note in _METRICS[kind]
    ]
    return pd.DataFrame(rows)


def builders() -> pd.DataFrame:
    """Every builder that has run over this store, and when it last did."""
    return frame("""
        SELECT parameters ->> 'builder' AS builder,
               max(started_at)::date    AS last_run,
               count(*)                 AS runs,
               (array_agg(counts ORDER BY started_at DESC))[1] AS last_counts
        FROM build_run
        WHERE parameters ? 'builder'
        GROUP BY 1 ORDER BY 2 DESC, 1
        """)


def finding_history() -> pd.DataFrame:
    """Findings per rule per QA run, oldest first.

    The arc is in the store: every run's findings are kept, so what the repairs
    did is a query rather than a note somebody wrote down.
    """
    return frame("""
        SELECT b.started_at, f.run_id, f.rule, count(*) AS findings
        FROM qa.finding f
        JOIN build_run b USING (run_id)
        GROUP BY 1, 2, 3
        ORDER BY 1, 3
        """)


# --------------------------------------------------------------------------
# pure helpers -- these take frames and numbers, never a connection
# --------------------------------------------------------------------------


def window(geometry, pad_m: float) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) around a geometry, padded in metres.

    The padding is applied in UTM and converted back, because a degree of
    longitude at 46 N is about 77 km against latitude's 111 km and padding both
    axes by the same number of degrees would leave the window a third short
    east-west.
    """
    box = gpd.GeoSeries([geometry], crs=CRS_STORAGE).to_crs(CRS_METRIC).buffer(pad_m)
    return tuple(box.to_crs(CRS_STORAGE).total_bounds)


def ordered_categories(values: pd.Series) -> list[str]:
    """The categories of a `*_class` column, in the order their prefix gives.

    The leading digit is what makes '1 flat (<5%)' sort before '2 gentle', and
    dropping it is how a legend ends up alphabetical with flat between gentle
    and moderate.
    """
    return sorted(str(v) for v in values.dropna().unique())


def category_colours(values: pd.Series) -> dict[str, str]:
    """A colour per category, following the order the prefixes impose."""
    categories = ordered_categories(values)
    if len(categories) == 2:
        return dict(zip(categories, [BLUE, RED]))
    palette = SEQUENCE * (len(categories) // len(SEQUENCE) + 1)
    return dict(zip(categories, palette))


def worst(frame_: pd.DataFrame, column: str, n: int = 1) -> pd.DataFrame:
    """The n rows a reviewer should look at first: the largest measure.

    The findings side of the review carries no class columns on purpose -- a
    duplicate, a bridge and a shared stretch look identical to a rule -- so
    "which one matters" is a sort on the raw measure, exactly as the bundle's
    sort_by says.
    """
    return frame_.nlargest(n, column)


def number(value) -> str:
    """A count for a label: thousands separated, and no .0 on a whole number.

    Postgres returns count() and round() as numeric, which arrives as a float,
    and "101,951.0 edges" reads like a measurement rather than a tally.
    """
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def as_text(value) -> str:
    """A Postgres array arrives as a list; a legend needs a string.

    A missing text column arrives as NaN rather than None once pandas has it,
    and str(nan) is "nan" -- which is how a route with no ref ends up labelled
    "nan - Via Mercatorum" in a legend.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v is not None)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------


#: What a figure is displayed at. Lower than print, high enough to read.
DISPLAY_DPI = 96


def _rendered(fig: plt.Figure, photographic: bool = False):
    """Lay a figure out, and hand its display to the value the cell returns.

    The inline backend shows every OPEN figure at the end of a cell, and the
    cell also displays whatever it returns -- so a builder that leaves its
    figure open renders the same picture twice. Closing it leaves exactly one.

    A figure with tiles under it goes out as JPEG, everything else as PNG.
    Aerial and street imagery is photographic and PNG stores it terribly: the
    category map is 674 KB as PNG against 135 KB as JPEG, and with five tiled
    maps that is the difference between a 15 MB commit and a 3 MB one every
    time this page is refreshed. Charts and the card panel stay PNG, because
    JPEG rings around text and thin lines.
    """
    fig.tight_layout()
    plt.close(fig)
    if not photographic:
        return fig
    try:
        from IPython.display import Image
    except ImportError:  # outside a notebook there is nothing to display to
        return fig
    buffer = io.BytesIO()
    fig.savefig(buffer, format="jpeg", dpi=DISPLAY_DPI, pil_kwargs={"quality": 82})
    return Image(data=buffer.getvalue(), format="jpeg")


def _finish(ax, title: str | None = None, width: int = 46) -> None:
    ax.set_axis_off()
    if title:
        ax.set_title(
            textwrap.fill(title, width), fontsize=9, color=INK, loc="left"
        )


def stat_tiles(state: pd.DataFrame, columns: int = 5) -> plt.Figure:
    """The state as numbers big enough to read across a room."""
    rows = (len(state) + columns - 1) // columns
    fig, axes = plt.subplots(rows, columns, figsize=(13, 1.6 * rows))
    for ax, (_, row) in zip(axes.flat, state.iterrows()):
        ax.set_axis_off()
        shown = number(row["value"])
        ax.text(0.5, 0.62, shown, ha="center", fontsize=19, color=BLUE, weight="bold")
        ax.text(0.5, 0.22, row["label"], ha="center", fontsize=9, color=INK)
    for ax in list(axes.flat)[len(state) :]:
        ax.set_axis_off()
    return _rendered(fig)


def network_map(
    network: gpd.GeoDataFrame,
    starts: gpd.GeoDataFrame | None = None,
    column: str = "steepness_class",
) -> plt.Figure:
    """The whole network in one frame, with the ingested regions drawn on it."""
    fig, ax = plt.subplots(figsize=(12, 9))
    network = _tiles(network)
    colours = category_colours(network[column])
    for category, colour in colours.items():
        part = network[network[column].astype(str) == category]
        part.plot(ax=ax, color=colour, linewidth=0.35)
    if starts is not None and len(starts):
        _tiles(starts).plot(ax=ax, color=INK, markersize=0.6, alpha=0.45)

    for name, (min_lat, min_lon, max_lat, max_lon) in REGIONS.items():
        # REGIONS is (min_lat, min_lon, max_lat, max_lon) -- latitude first,
        # the reverse of what a plot wants, and in 4326 rather than the CRS
        # this is drawn in.
        corners = gpd.GeoSeries(
            [
                LineString(
                    [
                        (min_lon, min_lat),
                        (max_lon, min_lat),
                        (max_lon, max_lat),
                        (min_lon, max_lat),
                        (min_lon, min_lat),
                    ]
                )
            ],
            crs=CRS_STORAGE,
        ).to_crs(CRS_TILES)
        corners.plot(ax=ax, color=INK, linewidth=0.9, linestyle="--", alpha=0.6)
        label = corners.iloc[0].coords[3]
        ax.text(label[0], label[1], f" {name}", fontsize=9, color=INK, va="bottom")

    handles = [
        Line2D([], [], color=colour, linewidth=2, label=category)
        for category, colour in colours.items()
    ]
    if starts is not None:
        handles.append(
            Line2D(
                [],
                [],
                color=INK,
                marker=".",
                linestyle="",
                label=f"{len(starts):,} starts",
            )
        )
    # Lower LEFT: the ingested regions leave that corner empty, and a legend
    # over the densest part of the map hides what it is labelling.
    ax.legend(handles=handles, loc="lower left", fontsize=8, frameon=False)
    ax.set_aspect("equal")
    _basemap(ax)
    _finish(ax)
    return _rendered(fig, photographic=True)


def history_chart(history: pd.DataFrame) -> plt.Figure:
    """Findings per rule across the QA runs, one panel per rule."""
    rules = sorted(history["rule"].unique())
    fig, axes = plt.subplots(1, len(rules), figsize=(13, 2.6), sharex=True)
    for ax, rule in zip(axes, rules):
        series = history[history["rule"] == rule].sort_values("started_at")
        ax.plot(
            range(len(series)),
            series["findings"],
            marker="o",
            markersize=3,
            color=RED if series["findings"].iloc[-1] else GREEN,
        )
        ax.set_title(rule, fontsize=9, color=INK)
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)
        ax.set_xticks([])
        last = series["findings"].iloc[-1]
        ax.annotate(
            number(last),
            (len(series) - 1, last),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=8,
            color=RED if last else GREEN,
        )
    return _rendered(fig)


def issues_bar(issues: pd.DataFrame) -> plt.Figure:
    """What is open, in the order it matters rather than the order of size."""
    fig, ax = plt.subplots(figsize=(9, 4))
    order = issues.iloc[::-1]  # first-listed at the top
    positions = range(len(order))
    ax.barh(
        list(positions),
        order["value"].clip(lower=0.6),
        color=[GREEN if v == 0 else SAND for v in order["value"]],
    )
    ax.set_yticks(list(positions))
    ax.set_yticklabels(order["label"], fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("count (log scale: these span 12 to 7,170)", fontsize=8)
    ax.grid(alpha=0.3, axis="x")
    for position, value in zip(positions, order["value"]):
        ax.annotate(
            number(value),
            (max(value, 0.6), position),
            textcoords="offset points",
            xytext=(6, -3),
            fontsize=8,
            color=INK,
        )
    return _rendered(fig)


#: The repairs that MOVE ground, and are therefore bound by the 2 m tolerance.
#: The other two rules do not move anything: gap_dangle_edge splits an edge at
#: a point and `degenerate` collapses a sub-metre one, so their start_moved_m /
#: end_moved_m measure the piece that resulted, not a distance the ground
#: travelled. Reading all four against one tolerance says a repair moved
#: something 555 m, which never happened.
SNAPPING_RULES = ["gap_dangle_pair", "gap_dangle_junction"]


def fix_histogram(fixes: pd.DataFrame, tolerance_m: float = 2.0) -> plt.Figure:
    """How far the snapping repairs moved an endpoint, against their tolerance."""
    snaps = fixes[fixes["rule"].isin(SNAPPING_RULES)]
    moved = pd.concat([snaps["start_moved_m"], snaps["end_moved_m"]]).dropna()
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.hist(moved, bins=30, color=BLUE)
    ax.axvline(tolerance_m, color=RED, linestyle="--", linewidth=1.2)
    ax.annotate(
        f"{tolerance_m:g} m tolerance — nothing crosses it",
        (tolerance_m, ax.get_ylim()[1] * 0.8),
        textcoords="offset points",
        xytext=(-8, 0),
        ha="right",
        fontsize=8,
        color=RED,
    )
    ax.set_xlabel("metres an endpoint moved", fontsize=8)
    ax.set_ylabel(f"{len(moved):,} endpoints", fontsize=8)
    ax.set_title(
        f"{len(snaps):,} snapping repairs "
        f"({', '.join(SNAPPING_RULES)}) — the ones that move ground",
        fontsize=9,
        color=INK,
        loc="left",
    )
    ax.grid(alpha=0.3)
    return _rendered(fig)


def findings_map(
    network: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame]
) -> plt.Figure:
    """Where the open findings are, over the network they are findings about."""
    fig, ax = plt.subplots(figsize=(12, 9))
    _tiles(network).plot(ax=ax, color=GREY, linewidth=0.2, alpha=0.5)
    for (name, layer), colour in zip(layers.items(), [RED, SAND, BLUE]):
        if not len(layer):
            continue
        _tiles(layer).plot(
            ax=ax, color=colour, markersize=6, linewidth=1.4, alpha=0.8
        )
        ax.plot([], [], color=colour, linewidth=2, label=f"{len(layer):,} {name}")
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    ax.set_aspect("equal")
    _basemap(ax)
    _finish(ax)
    return _rendered(fig, photographic=True)


def example_panel(
    ax,
    subject: gpd.GeoDataFrame,
    context: gpd.GeoDataFrame,
    title: str,
    colour: str = RED,
    before: gpd.GeoDataFrame | None = None,
    floor_deg: float = 0.0015,
    basemap=REFERENCE_TILES,
) -> None:
    """One zoomed example: the thing, and the network around it for context.

    Context is the network itself rather than a tile basemap -- it is what the
    finding is about, it needs no connection at render time, and it keeps the
    exported page self-contained.
    """
    subject, context = _tiles(subject), _tiles(context)
    if len(context):
        context.plot(ax=ax, color=GREY, linewidth=0.8, alpha=0.7)
    if before is not None and len(before):
        _tiles(before).plot(ax=ax, color=INK, linewidth=2.0, linestyle="--")
    # edgecolor as well as color: an island is a POLYGON a few tens of metres
    # across, and a fill that small disappears against the context under it.
    subject.plot(
        ax=ax,
        color=colour,
        edgecolor=colour,
        linewidth=2.2,
        markersize=28,
        alpha=0.85,
    )
    bounds = subject.total_bounds
    # The floor sets how far a panel can zoom in. A snap repair moves an
    # endpoint by two metres, and at the default window that is one pixel:
    # the panel has to be allowed to go closer than the finding is wide. In
    # Web Mercator the unit is a metre stretched by latitude, near enough at
    # 46 N for a window nobody measures off.
    floor = floor_deg * 111_320
    pad_x = max((bounds[2] - bounds[0]) * 0.6, floor)
    pad_y = max((bounds[3] - bounds[1]) * 0.6, floor)
    ax.set_xlim(bounds[0] - pad_x, bounds[2] + pad_x)
    ax.set_ylim(bounds[1] - pad_y, bounds[3] + pad_y)
    ax.set_aspect("equal")
    _basemap(ax, basemap)
    _finish(ax, title)


def examples_grid(rows: int = 2, columns: int = 3) -> tuple[plt.Figure, list]:
    """The frame the example panels are drawn into."""
    fig, axes = plt.subplots(rows, columns, figsize=(13, 8))
    return fig, list(axes.flat)


def catalogue_charts(draw: gpd.GeoDataFrame) -> plt.Figure:
    """The generated routes: what they are, and how they are graded."""
    fig = plt.figure(figsize=(13, 4))
    scatter_ax = fig.add_subplot(1, 3, 1)
    colours = category_colours(draw["offroad_class"])
    for category, colour in colours.items():
        part = draw[draw["offroad_class"].astype(str) == category]
        scatter_ax.scatter(
            part["km"], part["ascent_m"], s=14, color=colour, label=category, alpha=0.85
        )
    scatter_ax.set_xlabel("km", fontsize=8)
    scatter_ax.set_ylabel("ascent (m)", fontsize=8)
    scatter_ax.grid(alpha=0.3)
    scatter_ax.legend(fontsize=7, frameon=False)
    scatter_ax.tick_params(labelsize=7)

    for index, column in enumerate(["shape_class", "mtb_class"], start=2):
        ax = fig.add_subplot(1, 3, index)
        counts = draw[column].astype(str).value_counts().sort_index()
        ax.bar(range(len(counts)), counts.values, color=BLUE)
        ax.set_xticks(range(len(counts)))
        ax.set_xticklabels(counts.index, rotation=30, ha="right", fontsize=7)
        ax.set_title(column, fontsize=9, color=INK)
        ax.grid(alpha=0.3, axis="y")
        ax.tick_params(labelsize=7)
    return _rendered(fig)


def context_edges(geometry, pad_m: float = 150.0) -> gpd.GeoDataFrame:
    """The network around a thing, for drawing under it.

    The same idea the bundle's `network_context` layer uses at 150 m: a finding
    is unreadable without the ground it sits on, and the ground here is the
    network itself rather than a tile basemap -- which needs no connection at
    render time and keeps the exported page self-contained.
    """
    return geoframe(
        """
        SELECT e.edge_id, e.geom
        FROM curated.edge e
        WHERE ST_DWithin(
            e.geom::geography,
            ST_GeomFromText(%(wkt)s, 4326)::geography,
            %(pad)s)
        """,
        wkt=geometry.wkt,
        pad=pad_m,
    )


#: The six examples, and the measure that makes each one representative. The
#: findings side carries no class columns on purpose, so "which one matters" is
#: a sort on the raw measure -- the same sort the bundle tells a reviewer to
#: apply in QGIS.
EXAMPLES = [
    (
        "overlap",
        (
            "SELECT finding_id, shared_m, geom FROM qa.v_overlap "
            "ORDER BY shared_m DESC LIMIT 1"
        ),
        "shared_m",
        "the open queue's worst case: {measure:.0f} m mapped twice",
    ),
    (
        "island",
        (
            "SELECT finding_id, vertices, geom FROM qa.v_island "
            "ORDER BY vertices DESC LIMIT 1"
        ),
        "vertices",
        "the largest island: {measure:.0f} vertices reachable only from each other",
    ),
    (
        "place_link",
        (
            "SELECT source, kind, name, distance_m, geom FROM qa.v_place_link "
            "ORDER BY distance_m DESC LIMIT 1"
        ),
        "distance_m",
        "the longest snap: {measure:.0f} m from the feature to its vertex",
    ),
    (
        "draw",
        (
            "SELECT route_id, km, ascent_m, off_road_share, score, geom FROM qa.v_draw "
            "ORDER BY score DESC LIMIT 1"
        ),
        "km",
        "the best generated route: {measure:.1f} km",
    ),
]


def example(name: str, pad_m: float = 400.0) -> tuple:
    """One example: (subject, context, caption)."""
    _, sql, measure, caption = next(e for e in EXAMPLES if e[0] == name)
    subject = geoframe(sql)
    context = context_edges(subject.geometry.iloc[0], pad_m)
    return subject, context, caption.format(measure=subject[measure].iloc[0])


def repaired_example(pad_m: float = 120.0) -> tuple:
    """A repair, as the geometry before and the geometry after.

    Grey dashed is where the endpoint was; red is where it is. Nothing here
    should have moved more than the 2 m tolerance, which is what makes this
    panel a check rather than an illustration.
    """
    pick = (
        "FROM qa.v_fix WHERE rule = ANY(%(rules)s) ORDER BY end_moved_m DESC LIMIT 1"
    )
    after = geoframe(
        f"SELECT fix_id, rule, start_moved_m, end_moved_m, geom {pick}",
        rules=SNAPPING_RULES,
    )
    before = geoframe(
        f"SELECT fix_id, geom_before AS geom {pick}", rules=SNAPPING_RULES
    )
    context = context_edges(after.geometry.iloc[0], pad_m)
    moved = after["end_moved_m"].iloc[0]
    rule = after["rule"].iloc[0]
    return after, before, context, f"{rule}: an endpoint moved {moved:.2f} m"


def urban_exit_example(pad_m: float = 700.0) -> tuple:
    """A settlement's exits, trail and lane together.

    The judgement the exit rule asks for is comparative -- this way out is a
    walk, that one is the town continuing -- so the panel shows both rather
    than one.
    """
    busiest = geoframe(
        "SELECT vertex_id, name, way_out, exit_class, degree, geom "
        "FROM qa.v_urban_exit WHERE is_start ORDER BY degree DESC LIMIT 1"
    )
    around = geoframe(
        """
        SELECT vertex_id, way_out, exit_class, geom
        FROM qa.v_urban_exit
        WHERE ST_DWithin(geom::geography, ST_GeomFromText(%(wkt)s, 4326)::geography,
                         %(pad)s)
        """,
        wkt=busiest.geometry.iloc[0].wkt,
        pad=pad_m,
    )
    context = context_edges(busiest.geometry.iloc[0], pad_m)
    trail = around[around["exit_class"].str.startswith("0")]
    lane = around[around["exit_class"].str.startswith("1")]
    caption = f"one settlement's ways out: {len(trail)} onto trail, {len(lane)} onto lane"
    return trail, lane, context, caption


def examples_figure() -> plt.Figure:
    """Six panels: what each headline number looks like where it happened.

    A count is a claim about the ground. These are the six the review keeps
    coming back to, each drawn over the network around it and captioned with
    the measure that picked it.
    """
    fig, axes = examples_grid()

    subject, context, caption = example("overlap")
    example_panel(axes[0], subject, context, f"overlap — {caption}")

    subject, context, caption = example("island")
    example_panel(axes[1], subject, context, f"island — {caption}", colour=SAND)

    subject, context, caption = example("place_link")
    example_panel(axes[2], subject, context, f"snap — {caption}", colour=BLUE)

    after, before, context, caption = repaired_example(pad_m=90.0)
    example_panel(
        axes[3],
        after,
        context,
        f"fix — {caption}",
        before=before,
        # ~45 m. Closer than this and the tile providers run out: they stop at
        # zoom 20 and a 13 m window asks for 22, which comes back blurred.
        floor_deg=0.0004,
    )

    trail, lane, context, caption = urban_exit_example()
    if len(context):
        _tiles(context).plot(ax=axes[4], color=GREY, linewidth=0.8, alpha=0.7)
    _tiles(lane).plot(ax=axes[4], color=INK, markersize=26)
    _tiles(trail).plot(ax=axes[4], color=GREEN, markersize=34)
    axes[4].set_aspect("equal")
    _basemap(axes[4])
    _finish(axes[4], f"urban exits — {caption}")

    subject, context, caption = example("draw")
    example_panel(axes[5], subject, context, f"route — {caption}", colour=GREEN)

    return _rendered(fig, photographic=True)


#: How a route is attributed to an area: the regions its member edges carry.
#: 61 of 752 run through both, and they are their own group rather than being
#: forced into whichever one happens to hold more of them.
MAPPED_ROUTES = """
WITH area AS (
    SELECT er.rel_id,
           CASE WHEN count(DISTINCT reg) > 1 THEN 'Lecco + Bergamo'
                ELSE min(reg) END AS area
    FROM curated.edge_route er
    JOIN curated.edge e USING (edge_id),
         unnest(e.regions) AS reg
    GROUP BY er.rel_id
)
SELECT v.rel_id, v.ref, v.name, v.km, v.route_kind,
       v.continuity_class, a.area, v.geom
FROM qa.v_route v
JOIN area a USING (rel_id)
ORDER BY a.area, v.km DESC
"""

#: Panels left to right. The crossing routes last, because they are the
#: smallest group and the one that explains the other two.
AREA_ORDER = ["Lecco", "Bergamo", "Lecco + Bergamo"]


def mapped_routes() -> gpd.GeoDataFrame:
    """The 752 route relations OSM contributors have mapped, by area."""
    return geoframe(MAPPED_ROUTES)


def route_label(row) -> str:
    """What to call a route: its ref, its name, or plainly neither.

    650 of 752 carry a ref and 273 a name, so most have something; inventing
    one for the rest is the same decision nobody has made for the trailheads.
    """
    ref = as_text(row.get("ref"))
    name = as_text(row.get("name"))
    if ref and name:
        return f"{ref} — {name}"
    return ref or name or f"relation {row['rel_id']}"


def mapped_routes_map(routes: gpd.GeoDataFrame, label_top: int = 6) -> plt.Figure:
    """Every mapped route, grouped by area and coloured to be told apart.

    The colours identify a route AGAINST ITS NEIGHBOURS -- 449 of these share
    one panel, so no palette can name them all, and the job a colour does here
    is to let the eye follow one line where several cross. They cycle through
    twenty, assigned down the length order, so two routes that touch are
    almost never the same colour.

    The longest few in each area are named, because those are the ones a
    walker recognises: DOL, the Via Mercatorum, the Ciclovia Valle Brembana.
    """
    areas = [a for a in AREA_ORDER if a in set(routes["area"])]
    # Tall enough that the legends sit BELOW the maps: 449 routes fill their
    # panel corner to corner, so a legend inside the axes covers the thing it
    # is labelling.
    fig, axes = plt.subplots(1, len(areas), figsize=(14, 7.4))
    palette = plt.get_cmap("tab20").colors

    for ax, area in zip(axes, areas):
        here = _tiles(routes[routes["area"] == area])
        for position, (_, row) in enumerate(here.iterrows()):
            gpd.GeoSeries([row["geom"]], crs=CRS_TILES).plot(
                ax=ax, color=palette[position % len(palette)], linewidth=1.1
            )
        handles = [
            Line2D(
                [],
                [],
                color=palette[position % len(palette)],
                linewidth=2,
                label=f"{route_label(row)} · {row['km']:.0f} km",
            )
            for position, (_, row) in enumerate(here.head(label_top).iterrows())
        ]
        ax.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(0.0, -0.01),
            fontsize=6.5,
            frameon=False,
        )
        ax.set_aspect("equal")
        _basemap(ax)
        _finish(
            ax,
            f"{area} — {len(here)} routes, {here['km'].sum():,.0f} km",
            width=38,
        )
    return _rendered(fig, photographic=True)


def routes_by_kind(routes: gpd.GeoDataFrame) -> plt.Figure:
    """What kind of route was mapped, and how much of it, per area."""
    counts = (
        routes.groupby(["area", "route_kind"])["km"].sum().unstack(fill_value=0.0)
    )
    counts = counts.reindex([a for a in AREA_ORDER if a in counts.index])
    fig, ax = plt.subplots(figsize=(9, 3.2))
    bottom = None
    for column, colour in zip(counts.columns, [BLUE, GREEN, SAND, RED]):
        ax.barh(counts.index, counts[column], left=bottom, color=colour, label=column)
        bottom = counts[column] if bottom is None else bottom + counts[column]
    ax.set_xlabel("km mapped", fontsize=8)
    ax.legend(fontsize=8, frameon=False, ncol=4)
    ax.grid(alpha=0.3, axis="x")
    ax.tick_params(labelsize=8)
    return _rendered(fig)
# --------------------------------------------------------------------------
# one route, in full
# --------------------------------------------------------------------------

#: A route's edges in WALKING order, which is not the order the relation lists
#: them in. Measured on the Via Mercatorum: its member_index 88 sits at 99.93%
#: along the merged line, so the members run backwards and start two thirds of
#: the way through. Ordering by member_index put cliffs in the profile that are
#: not on the hill.
#:
#: ST_LineLocatePoint on the merged line is the same technique the pipeline
#: already uses to position a POI along a route. It needs ONE line, so a route
#: that comes out in several pieces falls back to member order and keeps
#: whatever the mapper's ordering is worth.
ROUTE_EDGES = """
WITH merged AS (
    SELECT ST_LineMerge(geom) AS line, ST_NumGeometries(geom) AS parts
    FROM qa.v_route WHERE rel_id = %(rel_id)s
)
SELECT er.member_index, er.piece_index, e.edge_id, e.length_m,
       e.profile_m, e.geom,
       CASE WHEN (SELECT parts FROM merged) = 1
            THEN ST_LineLocatePoint(
                     (SELECT line FROM merged),
                     ST_LineInterpolatePoint(e.geom, 0.5))
       END AS along
FROM curated.edge_route er
JOIN curated.edge e USING (edge_id)
WHERE er.rel_id = %(rel_id)s
ORDER BY along NULLS LAST, er.member_index, er.piece_index
"""

ROUTE_CARD = """
SELECT v.rel_id, v.ref, v.name, v.route_kind, v.network, v.osmc_symbol,
       v.km, v.edges, v.pieces, v.continuity_class, v.length_class,
       v.scope_class,
       el.ascent_m, el.descent_m, el.lowest_m, el.highest_m,
       el.edges_without_profile,
       c.way_members, c.matched_ways, c.matched_fraction, c.coverage_class
FROM qa.v_route v
LEFT JOIN qa.v_route_elevation el USING (rel_id)
LEFT JOIN qa.v_route_coverage c USING (rel_id)
WHERE v.rel_id = %(rel_id)s
"""


def route_edges(rel_id: int) -> gpd.GeoDataFrame:
    """A route's edges, in the order the relation lists them."""
    return geoframe(ROUTE_EDGES, rel_id=int(rel_id))


def route_card_data(rel_id: int) -> pd.Series:
    """Everything a card would carry about one route, from the three views."""
    return frame(ROUTE_CARD, rel_id=int(rel_id)).iloc[0]


def assemble_profile(edges: gpd.GeoDataFrame) -> tuple[list[float], list[float]]:
    """Distance and height along a route, from its edges' sampled profiles.

    Pure, and the interesting part is the orientation. A relation lists its
    ways in walking order but each way keeps the direction it was drawn in, so
    roughly half arrive backwards; concatenating their profiles as stored
    gives a saw of false climbs and descents. Each edge is oriented against
    the running end point before its samples are taken -- the same correction
    docs/route-document.md still has open for route assembly.

    A gap between pieces is not bridged. Distance keeps running, because the
    walk does, and the profile stays honest about not knowing what is in
    between.
    """
    distances: list[float] = []
    heights: list[float] = []
    travelled = 0.0
    cursor = None

    for _, row in edges.iterrows():
        line = row["geom"]
        if line is None or line.is_empty:
            continue
        profile = list(row["profile_m"] or [])

        # Orient first, off the geometry, so an edge with no samples still
        # leaves the cursor at the right end for the next one.
        coords = list(line.coords)
        start, end = coords[0], coords[-1]
        if cursor is not None:
            to_start = (start[0] - cursor[0]) ** 2 + (start[1] - cursor[1]) ** 2
            to_end = (end[0] - cursor[0]) ** 2 + (end[1] - cursor[1]) ** 2
            if to_end < to_start:
                profile = profile[::-1]
                start, end = end, start

        if len(profile) >= 2:
            step = row["length_m"] / (len(profile) - 1)
            for index, height in enumerate(profile):
                distances.append((travelled + index * step) / 1000.0)
                heights.append(height)

        # ALWAYS, even for an edge nothing was sampled on. 75 edges north of
        # the DEM's edge have no profile, and dropping their length as well as
        # their heights would shorten the route on the axis -- a gap makes the
        # climb unknown, not the walk shorter. The line drawn across such a
        # stretch is an absence, not a claim that it is flat.
        travelled += row["length_m"]
        cursor = end

    return distances, heights


def route_profile_card(rel_id: int) -> plt.Figure:
    """One route in full: the climb it asks for, and what a card would say.

    The profile is the thing distance alone cannot tell you -- 20 km along a
    lake and 20 km over a ridge are the same number and not the same day.
    """
    card = route_card_data(rel_id)
    distances, heights = assemble_profile(route_edges(rel_id))

    fig = plt.figure(figsize=(13, 4.2))
    profile_ax = fig.add_subplot(1, 3, (1, 2))
    profile_ax.fill_between(distances, heights, color=BLUE, alpha=0.25)
    profile_ax.plot(distances, heights, color=BLUE, linewidth=1.2)
    profile_ax.set_xlabel("km along the route", fontsize=8)
    profile_ax.set_ylabel("metres above sea level", fontsize=8)
    profile_ax.grid(alpha=0.3)
    profile_ax.tick_params(labelsize=8)
    profile_ax.set_title(route_label(card), fontsize=10, color=INK, loc="left")

    coverage = (
        f"{card['matched_ways']}/{card['way_members']} ways "
        f"({card['matched_fraction']:.0%})"
    )
    fields = [
        ("kind", as_text(card["route_kind"])),
        ("distance", f"{card['km']:.1f} km"),
        ("climb", f"{card['ascent_m']:,.0f} m up / {card['descent_m']:,.0f} m down"),
        ("between", f"{card['lowest_m']:,.0f} m and {card['highest_m']:,.0f} m"),
        ("made of", f"{card['edges']:,} edges, {card['pieces']} piece(s)"),
        ("continuity", as_text(card["continuity_class"])),
        ("coverage", coverage),
        ("scope", as_text(card["scope_class"])),
        ("waymarked", as_text(card["osmc_symbol"]) or "not recorded"),
        ("network", as_text(card["network"]) or "not recorded"),
    ]
    card_ax = fig.add_subplot(1, 3, 3)
    card_ax.set_axis_off()
    card_ax.set_title("what a card would carry", fontsize=10, color=INK, loc="left")
    for position, (label, value) in enumerate(fields):
        height = 0.94 - position * 0.095
        card_ax.text(0.0, height, label, fontsize=8, color=GREY)
        card_ax.text(0.34, height, value, fontsize=8.5, color=INK)
    return _rendered(fig)


# --------------------------------------------------------------------------
# routes up close, and by category
# --------------------------------------------------------------------------


def route_closeups(
    routes: gpd.GeoDataFrame, rel_ids: list[int], pad_m: float = 900.0
) -> plt.Figure:
    """A few routes over the earth they cross.

    The reference map says where a line is; the imagery says what it goes
    through -- forest, scree, a lake shore, somebody's field. That is the
    question a walker actually has, and the one a categorised colour cannot
    answer. Drawn twice, a wide pale line under a thin dark one, because a
    single stroke over aerial imagery disappears into it.
    """
    picks = routes[routes["rel_id"].isin(rel_ids)]
    fig, axes = plt.subplots(1, max(len(picks), 1), figsize=(14, 5.4))
    axes = [axes] if len(picks) <= 1 else list(axes)

    for ax, (_, row) in zip(axes, picks.iterrows()):
        drawn = _tiles(gpd.GeoDataFrame([row], geometry="geom", crs=CRS_STORAGE))
        drawn.plot(ax=ax, color="#ffe14d", linewidth=3.0)
        drawn.plot(ax=ax, color=RED, linewidth=1.1)
        bounds = drawn.total_bounds
        pad = max(pad_m, (bounds[2] - bounds[0]) * 0.05)
        ax.set_xlim(bounds[0] - pad, bounds[2] + pad)
        ax.set_ylim(bounds[1] - pad, bounds[3] + pad)
        ax.set_aspect("equal")
        _basemap(ax, IMAGERY_TILES)
        _finish(ax, f"{route_label(row)} · {row['km']:.0f} km", width=40)
    return _rendered(fig, photographic=True)


def routes_by_category(
    routes: gpd.GeoDataFrame, column: str = "route_kind"
) -> plt.Figure:
    """Every mapped route on one map, coloured by what kind of route it is.

    The area panels answer "where"; this answers "what". Two questions, and
    one map cannot carry both without becoming a mess.
    """
    drawn = _tiles(routes)
    categories = sorted(drawn[column].dropna().unique())
    colours = dict(zip(categories, [BLUE, RED, GREEN, SAND, INK]))

    fig, ax = plt.subplots(figsize=(12, 8))
    # Commonest first, so the 25 mtb relations are not buried under the 684
    # hiking ones.
    for category in sorted(categories, key=lambda c: -(drawn[column] == c).sum()):
        part = drawn[drawn[column] == category]
        part.plot(ax=ax, color=colours[category], linewidth=1.0, alpha=0.9)
        ax.plot(
            [],
            [],
            color=colours[category],
            linewidth=2,
            label=f"{category} — {len(part)} routes, {part['km'].sum():,.0f} km",
        )
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    ax.set_aspect("equal")
    _basemap(ax)
    _finish(ax)
    return _rendered(fig, photographic=True)
