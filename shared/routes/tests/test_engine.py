"""The pack engine: CSR arcs, bounded fields, penalties, shapes, invariants."""

from pathlib import Path

import numpy as np
import pytest
from test_pack import synthetic

from vaivia_routes import pack
from vaivia_routes.assemble import WalkedEdge, assemble, assert_connected
from vaivia_routes.draw import draw_loop, draw_strict_out_and_back
from vaivia_routes.invariants import mini_loops, spurs
from vaivia_routes.network import Network


def synthetic_pack() -> pack.Pack:
    arrays, manifest = synthetic()
    manifest["format"] = pack.FORMAT
    pack.validate(arrays, manifest)
    return pack.Pack(manifest=manifest, arrays=arrays)


#     0 -- 1
#     |  / |          edge ids 100..104: (0,1) (1,3) (2,3) (0,2) (1,2)
#     2 -- 3          edge 1-2 bike-only, one way (foot forbidden both ways)
def test_foot_route_avoids_the_forbidden_edge():
    net = Network.build(synthetic_pack(), "foot")
    steps = net.route(1, 2)
    # 1→0 (edge 0 backwards, 100) + 0→2 (edge 3 forwards, 80) beats 1→3→2 (220)
    assert steps == [(0, False), (3, True)]
    walked = net.walked(steps)
    assert [w.edge_id for w in walked] == [100, 103]
    assert_connected(walked)
    facts = assemble(walked)
    assert facts.distance_m == pytest.approx(180.0)


def test_bike_takes_the_oneway_but_strict_shape_may_not():
    net = Network.build(synthetic_pack(), "mtb")
    assert net.route(1, 2) == [(4, True)]  # the 50-cost oneway
    assert net.route(2, 1) != [(4, False)]  # its reverse does not exist
    # strict out-and-back must come home legally: the oneway is off the table
    steps = draw_strict_out_and_back(net, 1, 2)
    assert steps is not None
    assert all(e.edge_id != 104 for e in steps)
    assert_connected(steps)
    # the return is the out reversed, by construction
    assert [e.edge_id for e in steps] == [e.edge_id for e in reversed(steps)]


def test_penalty_makes_the_return_take_the_other_way():
    net = Network.build(synthetic_pack(), "foot")
    out = net.route(0, 3)  # 0→1→3 (220) vs 0→2→3 (180): via 2
    assert out == [(3, True), (2, True)]
    back = net.route(3, 0, penalised={i for i, _f in out})
    # penalised ×3 the way it came (540), so home through 1 (220)
    assert back == [(1, False), (0, False)]


def test_field_is_bounded():
    net = Network.build(synthetic_pack(), "foot")
    dist = net.field_from(0, limit=150.0)
    assert dist[0] == 0
    assert dist[2] == pytest.approx(80.0)
    assert dist[1] == pytest.approx(100.0)
    assert np.isinf(dist[3])  # 180 via 2, beyond the limit


def test_nearest_vertex_stays_on_the_main_component():
    net = Network.build(synthetic_pack(), "foot")
    assert net.nearest_vertex(9.3801, 45.8501) == 0


FIXTURE = Path(__file__).parent / "fixtures" / "pack-lecco-3km"


def test_a_loop_draws_over_the_fixture_pack():
    net = Network.build(pack.load(FIXTURE), "foot")
    start = net.pack["place_vertex"][net.pack["place_is_start"]][0]
    lon = float(net.pack["vertex_lon"][start])
    lat = float(net.pack["vertex_lat"][start])
    for seed in range(4):
        walked = draw_loop(net, int(start), (lon, lat), 5000.0, seed)
        if walked is None:
            continue
        assert_connected(walked)
        facts = assemble(walked)
        assert facts.distance_m > 0
        assert facts.coords[0] == facts.coords[-1]  # it is a loop
        break
    else:
        pytest.fail("no seed drew a loop from the fixture's first start")


def we(edge_id: int, forward: bool, source: int, target: int) -> WalkedEdge:
    return WalkedEdge(
        edge_id=edge_id,
        forward=forward,
        length_m=100.0,
        coords=[(0.0, 0.0), (1.0, 1.0)],
        profile_m=None,
        ascent_m=0.0,
        descent_m=0.0,
        surface=None,
        sac_scale=None,
        mtb_scale=None,
        highway=None,
        routable_bike=True,
        source=source,
        target=target,
    )


def test_assert_connected_names_the_hole():
    good = [we(1, True, 0, 1), we(2, True, 1, 2), we(3, False, 3, 2)]
    assert_connected(good)
    with pytest.raises(ValueError, match="step 1"):
        assert_connected([we(1, True, 0, 1), we(2, True, 5, 6)])


def test_mini_loop_found_but_the_walks_own_closure_is_not():
    # 0→1→2→1 within 300 m: the walk returns to 1 — a mini loop
    walk = [we(1, True, 0, 1), we(2, True, 1, 2), we(3, True, 2, 1), we(4, True, 1, 3)]
    assert mini_loops(walk, within_m=300.0) == [(1, 2, 200.0)]
    assert mini_loops(walk, within_m=150.0) == []
    # a whole loop closing at its start is not a finding
    closure = [we(1, True, 0, 1), we(2, True, 1, 2), we(3, True, 2, 0)]
    assert mini_loops(closure, within_m=1e9) == []


def test_spur_is_the_grown_palindrome():
    # out 1,2,3 then straight back 3,2 then away: spur is steps 1..4
    walk = [
        we(1, True, 0, 1),
        we(2, True, 1, 2),
        we(3, True, 2, 3),
        we(3, False, 2, 3),
        we(2, False, 1, 2),
        we(9, True, 1, 9),
    ]
    assert spurs(walk) == [(1, 4, 400.0)]
    # an out-and-back is one big palindrome — the caller exempts the shape
    strict = [
        we(1, True, 0, 1),
        we(2, True, 1, 2),
        we(2, False, 1, 2),
        we(1, False, 0, 1),
    ]
    assert spurs(strict) == [(0, 3, 400.0)]
