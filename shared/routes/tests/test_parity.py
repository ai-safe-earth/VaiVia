"""The 627-id parity test: the pack engine reproduces the catalogue.

Replays every catalogue ask — (start, target, seed) for loops, (start,
destination) for the rest — over the full exported pack and demands the
same geometry-derived route id the pgRouting factory minted. This is what
lets on-demand routes ship without a human review: same asks, same routes,
checked, not hoped (docs/route-design.md, decision 1).

Needs artefacts CI does not have: a full pack and the asks dump, produced
against the same store —

    cd pipeline
    uv run python -m export.pack --name pack-parity
    uv run python -m export.parity

then run with VAIVIA_PACK_DIR / VAIVIA_PARITY_ASKS pointing at them
(defaults: pipeline/packs/pack-parity, pipeline/packs/parity_asks.json).
Skipped when either is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vaivia_routes.assemble import assemble, assert_connected
from vaivia_routes.draw import draw_loop, draw_out_and_back, draw_strict_out_and_back
from vaivia_routes.ids import DIRECTED_SHAPES, forward_is_stored, route_id
from vaivia_routes.network import Network
from vaivia_routes.pack import load

PIPELINE_PACKS = Path(__file__).resolve().parents[3] / "pipeline" / "packs"
PACK_DIR = Path(os.environ.get("VAIVIA_PACK_DIR", PIPELINE_PACKS / "pack-parity"))
ASKS = Path(os.environ.get("VAIVIA_PARITY_ASKS", PIPELINE_PACKS / "parity_asks.json"))

pytestmark = pytest.mark.skipif(
    not (PACK_DIR.is_dir() and ASKS.is_file()),
    reason="full pack or asks dump not present (see module docstring)",
)


def test_the_pack_engine_reproduces_all_catalogue_ids():
    pack = load(PACK_DIR)
    asks = json.loads(ASKS.read_text(encoding="utf-8"))
    vertex_index = {int(v): i for i, v in enumerate(pack["vertex_id"])}
    networks = {a: Network.build(pack, a) for a in {ask["activity"] for ask in asks}}

    mismatches: list[str] = []
    for ask in asks:
        net = networks[ask["activity"]]
        start = vertex_index[ask["start_vertex"]]
        if ask["shape"] == "loop":
            walked = draw_loop(
                net,
                start,
                (ask["start_lon"], ask["start_lat"]),
                ask["target_m"],
                ask["seed"],
            )
        elif ask["shape"] == "out_and_back":
            walked = draw_strict_out_and_back(
                net, start, vertex_index[ask["destination_vertex"]]
            )
        else:
            walked = draw_out_and_back(
                net, start, vertex_index[ask["destination_vertex"]]
            )
        if walked is None:
            mismatches.append(f"{ask['route_id']}: nothing drawn")
            continue
        assert_connected(walked)
        facts = assemble(walked)
        direction = "fwd"
        if ask["shape"] in DIRECTED_SHAPES:
            direction = "fwd" if forward_is_stored(facts.coords) else "rev"
        minted = route_id([facts.coords], ask["shape"], direction)
        if minted != ask["route_id"]:
            mismatches.append(f"{ask['route_id']}: drew {minted}")

    assert not mismatches, (
        f"{len(mismatches)} of {len(asks)} asks did not reproduce their id:\n"
        + "\n".join(mismatches[:20])
    )
