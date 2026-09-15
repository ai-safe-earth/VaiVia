"""The loaded pack and its routing networks — built once at startup.

The backend draws outings in-process over the exported pack
(docs/route-design.md): ~35 MB of arrays, one CSR per activity, all
resident. Loading validates every invariant, so a backend that boots is a
backend that routes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from vaivia_routes.network import Network
from vaivia_routes.pack import Pack, load

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannerState:
    pack: Pack
    networks: dict[str, Network]  # cost layer ("foot" / "mtb") -> Network

    @property
    def run_id(self) -> str:
        return self.pack.run_id

    @property
    def gazetteer(self) -> list[dict]:
        return self.pack.manifest.get("gazetteer") or []


def load_planner(pack_dir: str) -> PlannerState:
    pack = load(Path(pack_dir))
    networks = {a: Network.build(pack, a) for a in ("foot", "mtb")}
    logger.info(
        "pack loaded",
        extra={"pack_run_id": pack.run_id, "counts": pack.counts},
    )
    return PlannerState(pack=pack, networks=networks)
