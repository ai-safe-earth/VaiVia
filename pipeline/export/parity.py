"""Dump the catalogue's asks for the pack-engine parity test.

Every catalogue route records the ask that drew it — start vertex, target,
seed or destination — and the parity test (shared/routes/tests/test_parity.py)
replays those asks over the exported pack and demands the same route ids
(docs/route-design.md, decision 1). This dump is the test's fixture: run it
against the same store the pack was exported from, then point the test at
both with VAIVIA_PACK_DIR / VAIVIA_PARITY_ASKS.

    uv run python -m export.parity            # writes packs/parity_asks.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core import connect

ASKS = """
SELECT r.route_id, r.activity, r.shape, r.direction, r.start_vertex,
       ST_X(v.geom), ST_Y(v.geom), r.target_m, r.seed,
       r.destination_id, p.vertex_id
FROM catalogue.route r
JOIN source_map.vertex v ON v.vertex_id = r.start_vertex
LEFT JOIN source_map.place p
  ON p.source || ':' || p.source_id = r.destination_id
ORDER BY r.route_id
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="packs/parity_asks.json")
    args = parser.parse_args()
    with connect() as conn:
        rows = conn.execute(ASKS).fetchall()
    asks = [
        {
            "route_id": route_id,
            "activity": activity,
            "shape": shape,
            "direction": direction,
            "start_vertex": start_vertex,
            "start_lon": lon,
            "start_lat": lat,
            "target_m": target_m,
            "seed": seed,
            "destination_id": destination_id,
            "destination_vertex": destination_vertex,
        }
        for (
            route_id,
            activity,
            shape,
            direction,
            start_vertex,
            lon,
            lat,
            target_m,
            seed,
            destination_id,
            destination_vertex,
        ) in rows
    ]
    missing = [
        a["route_id"]
        for a in asks
        if a["shape"] != "loop" and a["destination_vertex"] is None
    ]
    if missing:
        raise SystemExit(
            f"{len(missing)} destination routes whose place is gone: {missing[:5]}"
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asks, indent=1), encoding="utf-8")
    print(f"wrote {len(asks)} asks to {out}")


if __name__ == "__main__":
    main()
