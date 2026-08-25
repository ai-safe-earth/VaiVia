"""One-shot rekey of catalogue.route to the v2 geometry-derived ids.

The id cutover (approved plan, P3): every route kind takes a
`vv2-<digest>[:fwd|:rev]` id minted by pipeline/ids.py, so the table the
generated documents are emitted from must speak the same ids as the
documents, or the emitter's drift guard refuses to run. Idempotent: rows
already carrying a vv2- id are left alone. catalogue.route_edge rows follow
their route inside the same transaction.

Run from pipeline/ (before draw.emit, after migrating):
    uv run python rekey_v2.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json

from core import connect
from ids import DIRECTED_SHAPES, forward_is_stored, route_id

ROWS = """
SELECT route_id, shape, ST_AsGeoJSON(geom)
FROM catalogue.route
ORDER BY route_id
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        rows = conn.execute(ROWS).fetchall()
        mapping: list[tuple[str, str, str | None]] = []
        for old_id, shape, geojson in rows:
            if old_id.startswith("vv2-"):
                continue
            coords = json.loads(geojson)["coordinates"]
            direction = None
            if shape in DIRECTED_SHAPES:
                direction = "fwd" if forward_is_stored(coords) else "rev"
            mapping.append(
                (old_id, route_id([coords], shape, direction or "fwd"), direction)
            )

        print(f"{len(rows)} routes, {len(mapping)} to rekey")
        collisions = len(mapping) - len({new for _old, new, _d in mapping})
        if collisions:
            raise SystemExit(
                f"{collisions} ground collisions — two rows hash to one id; "
                "they are the same ground and the table must dedupe first"
            )
        if args.dry_run:
            for old_id, new_id, direction in mapping[:10]:
                print(f"  {old_id} -> {new_id} ({direction or 'undirected'})")
            print("--dry-run: nothing written")
            return

        with conn.transaction():
            # route_edge's FK references route(route_id) without ON UPDATE
            # CASCADE, so the key swap happens with the constraint out of the
            # way and back in force before the commit — the transaction is
            # what makes that safe.
            conn.execute(
                "ALTER TABLE catalogue.route_edge"
                " DROP CONSTRAINT route_edge_route_id_fkey"
            )
            for old_id, new_id, direction in mapping:
                conn.execute(
                    "UPDATE catalogue.route SET route_id = %s, direction = %s"
                    " WHERE route_id = %s",
                    (new_id, direction, old_id),
                )
                conn.execute(
                    "UPDATE catalogue.route_edge SET route_id = %s WHERE route_id = %s",
                    (new_id, old_id),
                )
            conn.execute(
                "ALTER TABLE catalogue.route_edge"
                " ADD CONSTRAINT route_edge_route_id_fkey"
                " FOREIGN KEY (route_id) REFERENCES catalogue.route (route_id)"
                " ON DELETE CASCADE"
            )
        print(f"rekeyed {len(mapping)} routes and their walked sequences")


if __name__ == "__main__":
    main()
