"""The cross-layer contract fixture: one document both tiers must agree on.

The pipeline suite asserts `build_document` reproduces this file byte for
byte; the backend suite serves the SAME file through its document reader and
asserts every read path. A schema change that breaks a reader now fails a
build instead of 500ing under a user's click — the enforcement the field-
ownership table in docs/route-document.md names.

Inputs are fixed literals (no database), so the output is deterministic by
the same rule as every emitted document: same input, byte-identical file.

Run from pipeline/ after any schema or emitter change:
    uv run python emit_contract_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

from export.document import Span, build_document

OUT = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "fixtures"
    / "route_document_contract.json"
)

#: The canonical inputs. Every block the schema requires is exercised,
#: including the ones the API reads structurally (geometry, measures,
#: continuity, surface, places, profile, provenance).
CONTRACT_KWARGS = {
    "route_id": "vv2-c0ffeec0ffeec0ff:fwd",
    "kind": "osm_route",
    "shape": "circular",
    "direction": "fwd",
    "identity": {
        "name": "Anello del Contratto",
        "ref": "42",
        "activity": "hiking",
        "network": "lwn",
        "waymark": "red:red:white_stripe:42:black",
        "from": None,
        "to": None,
        "operator": None,
        "regions": ["Bergamo"],
        "osm_relation_id": 4242,
    },
    "geometry": {
        "type": "LineString",
        "coordinates": [[9.67, 45.7], [9.68, 45.71], [9.69, 45.7], [9.67, 45.7]],
    },
    "bbox": [9.67, 45.7, 9.69, 45.71],
    "distance_m": 9500.0,
    "ascent_m": 620.0,
    "descent_m": 620.0,
    "lowest_m": 310.0,
    "highest_m": 905.0,
    "profile": {
        "distance_m": [0.0, 4750.0, 9500.0],
        "elevation_m": [310.0, 905.0, 310.0],
    },
    "surface_spans": [
        Span("gravel", 6000.0),
        Span("paved", 2000.0),
        Span(None, 1500.0),
    ],
    "sac_spans": [Span("mountain_hiking", 6000.0), Span("hiking", 3000.0)],
    "pieces": 1,
    "edges_without_profile": 0,
    "matched_fraction": 0.97,
    "places": [
        {
            "id": "n4242",
            "kind": "peak",
            "name": "Cima del Contratto",
            "ele_m": 905.0,
            "lon": 9.68,
            "lat": 45.71,
            "offset_m": 8.0,
            "distance_along_m": 4750.0,
            "is_start": False,
        }
    ],
    "terminals": [
        {
            "vertex_id": 4242,
            "point": {"type": "Point", "coordinates": [9.67, 45.7]},
            "names": ["Parcheggio del Contratto"],
            "start_classes": ["parking", "station"],
            "car_free": True,
            "nearest_start_m": 0.0,
            "reachable": True,
            "seasons": {
                "spring": True,
                "summer": True,
                "autumn": True,
                "winter": True,
                "unverified": False,
            },
        }
    ],
    "divergence": {"vertex_id": 4243, "approach_m": 412.5},
    "provenance": {
        "run_id": "export-contract",
        "producer": "pipeline/emit_contract_fixture.py",
        "sources": [
            {
                "name": "OpenStreetMap",
                "licence": "ODbL 1.0",
                "attribution": "© OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/copyright",
                "provides": ["geometry"],
            }
        ],
    },
}


def contract_document() -> dict:
    return build_document(**CONTRACT_KWARGS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(contract_document(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
