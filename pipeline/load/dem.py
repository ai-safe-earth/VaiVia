"""Load the Copernicus GLO-30 tile into staging.dem, tiled for sampling.

raster2pgsql is absent from the pgrouting image, so the load is plain SQL:
ST_FromGDALRaster reads the GeoTIFF bytes server-side and ST_Tile cuts it into
256x256 tiles — the shape ST_Value sampling wants, one small tile per lookup
instead of one 44 MB raster row. GDAL drivers are disabled by default in
PostGIS for good security reasons; GTiff is enabled for this session only.

The COG also stays on disk (pipeline/data/) for rasterio-side work — tests and
the profile comparison read the file; production profile sampling reads the
database.

Run from pipeline/:
    uv run python -m load.dem --tif data/glo30_N45_E009.tif
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from core import connect

CREATE = """
CREATE TABLE IF NOT EXISTS staging.dem (
    rid    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL,
    rast   raster NOT NULL,
    run_id text NOT NULL
)
"""

LOAD = """
INSERT INTO staging.dem (source, rast, run_id)
SELECT %(source)s, ST_Tile(ST_FromGDALRaster(%(data)s::bytea, 4326), 256, 256),
       %(run_id)s
"""

INDEX = """
CREATE INDEX IF NOT EXISTS dem_rast_idx
    ON staging.dem USING gist (ST_ConvexHull(rast))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tif", required=True)
    args = parser.parse_args()

    path = Path(args.tif)
    data = path.read_bytes()
    run_id = f"load-dem-{uuid.uuid4().hex[:8]}"

    with connect() as conn:
        conn.execute(
            "INSERT INTO build_run (run_id, stage, parameters) VALUES (%s, 'load', %s)",
            (run_id, json.dumps({"source": path.name, "loader": "dem"})),
        )
        conn.execute(CREATE)
        conn.execute("DELETE FROM staging.dem WHERE source = %s", (path.name,))
        conn.execute("SET postgis.gdal_enabled_drivers = 'GTiff'")
        conn.execute(LOAD, {"source": path.name, "data": data, "run_id": run_id})
        conn.execute(INDEX)

        tiles, (mn, mx) = (
            conn.execute(
                "SELECT count(*) FROM staging.dem WHERE source = %s", (path.name,)
            ).fetchone()[0],
            conn.execute(
                """SELECT min((ST_SummaryStats(rast)).min),
                          max((ST_SummaryStats(rast)).max)
                   FROM staging.dem WHERE source = %s""",
                (path.name,),
            ).fetchone(),
        )
        conn.execute(
            "UPDATE build_run SET finished_at = now(), counts = %s WHERE run_id = %s",
            (json.dumps({"tiles": tiles, "min_ele": mn, "max_ele": mx}), run_id),
        )
        print(f"{path.name}: {tiles} tiles, elevation range {mn:.0f}..{mx:.0f} m")

        # Summits a local can check (coordinates from the graph's OSM peaks).
        for name, lat, lon, expect in (
            ("Punta Cermenati (Resegone)", 45.8584126, 9.4688633, 1875),
            ("Grigna Settentrionale", 45.9533979, 9.3876756, 2410),
        ):
            sample = conn.execute(
                """SELECT ST_Value(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
                   FROM staging.dem
                   WHERE ST_Intersects(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
                     AND source = %s""",
                (lon, lat, lon, lat, path.name),
            ).fetchone()
            if sample and sample[0] is not None:
                print(f"{name}: {sample[0]:.0f} m (expect ~{expect})")


if __name__ == "__main__":
    main()
