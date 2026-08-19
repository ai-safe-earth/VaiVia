"""Plot the near-miss distribution, so a snapping tolerance is chosen by looking.

The question a tolerance answers is "below what distance is a loose end near
another line a MISTAKE rather than a real dead end?" That is not answerable
from a count; it is answerable from the shape of the distribution, and from
opening a few examples in QGIS at each candidate distance.

Writes a PNG next to the data (pipeline/data/, gitignored) — an artefact to
look at, not a number to trust.

Run from pipeline/:
    uv run python -m topology.histogram
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display in this environment
import matplotlib.pyplot as plt

from core import connect
from topology.qa import NEAR_MISS_DISTANCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-m", type=float, default=100.0)
    parser.add_argument("--out", default="data/near_miss.png")
    args = parser.parse_args()

    with connect() as conn:
        rows = conn.execute(NEAR_MISS_DISTANCES, {"max_m": args.max_m}).fetchall()
    values = [float(r[0]) for r in rows if r[0] is not None]
    if not values:
        raise SystemExit("no near misses measured")

    fig, (ax, cum) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)

    ax.hist(values, bins=100, range=(0, args.max_m), color="#4a6fa5")
    ax.set_ylabel("loose ends")
    ax.set_title(
        f"Distance from each loose end to the nearest edge it is NOT joined to\n"
        f"{len(values):,} of {len(rows):,} loose ends have anything within "
        f"{args.max_m:.0f} m"
    )
    ax.grid(alpha=0.3)

    ordered = sorted(values)
    cum.plot(
        ordered,
        [100 * (i + 1) / len(rows) for i in range(len(ordered))],
        color="#a5484a",
    )
    cum.set_xlabel("distance to nearest unconnected edge (m)")
    cum.set_ylabel("% of all loose ends")
    cum.grid(alpha=0.3)

    # Candidate tolerances, so the cost of each is visible rather than argued.
    for candidate in (1, 2, 5, 10):
        n = sum(1 for v in values if v <= candidate)
        for axis in (ax, cum):
            axis.axvline(candidate, color="#888", linestyle="--", linewidth=0.8)
        cum.annotate(
            f"{candidate} m → {n:,}",
            (candidate, 100 * n / len(rows)),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=8,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")
    print(
        "A tolerance is defensible where the curve is FLAT below it: a rise "
        "means real dead ends are being welded to whatever they pass near."
    )


if __name__ == "__main__":
    main()
