import numpy as np
import pytest

from aircraft_foundry.core.components import ComponentType as CT, Relation
from aircraft_foundry.core.design import AircraftDesign
from aircraft_foundry.core.parameters import AircraftParameters
from aircraft_foundry.geometry import builder
from aircraft_foundry.geometry.airfoil import NacaAirfoil
from aircraft_foundry.geometry.loft import loft_to_mesh


def design(**mods):
    p = AircraftParameters()
    for k, v in mods.items():
        p.set(k.replace("__", "."), v)
    return AircraftDesign(p)


def test_generator_dependencies_cover_component_sources():
    """A component may only read parameter groups its generator declares (else edits leave stale geometry)."""
    d = design()
    for g in builder.GENERATORS:
        for c in d._by_generator[g.name]:
            assert set(c.source_groups) <= g.depends_on, (g.name, c.id)


def test_all_control_surfaces_present_and_hinged():
    d = design()
    for cid, t in [("wing.right.aileron", CT.AILERON), ("wing.left.flap", CT.FLAP),
                   ("htail.right.elevator", CT.ELEVATOR), ("vtail.rudder", CT.RUDDER)]:
        c = d.component(cid)
        assert c.type == t
        assert {k.relation for k in c.connections} == {Relation.HINGED_TO}
        assert c.parameters["area_m2"] > 0
        assert any(x.startswith(f"attach.hinge.{cid}.") for x in d.components)


def test_split_airfoil_loops():
    af = NacaAirfoil(0.02, 0.4, 0.12)
    fore = af.fore_loop(0.7)
    assert len(fore) == len(af.loop()) and fore[len(fore) // 2, 0] == pytest.approx(0, abs=1e-12)
    assert fore[:, 0].max() <= 0.71
    aft, corners = af.aft_loop(0.71)
    assert corners == (0, 12, 13, 25) and aft[:, 0].min() >= 0.70


@pytest.mark.parametrize("mods", [{}, {"controls__flap_deflection_deg": 35, "controls__aileron_deflection_deg": -20,
                                      "controls__elevator_deflection_deg": 15, "controls__rudder_deflection_deg": -25}])
def test_control_and_skin_solids_closed(mods):
    d = design(**mods)
    for c in d.components.values():
        if c.type.is_control_surface or c.type in (CT.WING, CT.HORIZONTAL_TAIL, CT.VERTICAL_TAIL):
            for lo in c.lofts:
                m = loft_to_mesh(lo)
                assert m.is_closed_manifold() and m.signed_volume() > 0, c.id


def _zmin(d, cid):
    return d.component(cid).world_mesh().vertices[:, 2].min()


def test_deflection_sense():
    d0 = design()
    d = design(controls__flap_deflection_deg=30, controls__aileron_deflection_deg=15,
               controls__elevator_deflection_deg=10, controls__rudder_deflection_deg=20)
    assert _zmin(d, "wing.right.flap") < _zmin(d0, "wing.right.flap") - 0.2  # flaps down
    assert _zmin(d, "wing.right.aileron") < _zmin(d0, "wing.right.aileron")  # right aileron TE down
    zmax = lambda dd: dd.component("wing.left.aileron").world_mesh().vertices[:, 2].max()  # noqa: E731
    assert zmax(d) > zmax(d0)  # left aileron TE up (differential)
    assert _zmin(d, "htail.left.elevator") < _zmin(d0, "htail.left.elevator")  # both elevators down
    ymin = lambda dd: dd.component("vtail.rudder").world_mesh().vertices[:, 1].min()  # noqa: E731
    assert ymin(d) < ymin(d0)  # rudder TE to port


def test_deflection_keeps_hinge_line_fixed():
    d0, d = design(), design(controls__flap_deflection_deg=40)
    h0 = [k.point for k in d0.component("wing.right.flap").connections]
    h1 = [k.point for k in d.component("wing.right.flap").connections]
    assert np.allclose(h0, h1)


def test_flap_edit_recuts_skin_only_where_needed():
    d = design()
    before = d.component("wing.right").geometry_fingerprint
    cs = d.set_parameter("controls.flap_chord", 0.34)
    assert d.component("wing.right").geometry_fingerprint != before
    assert not any(x.startswith("fuselage") for x in cs.touched)
    cs = d.set_parameter("controls.rudder_deflection_deg", 10.0)
    assert cs.updated == ["vtail.rudder"]  # deflection moves only the rudder


def test_flaps_raise_clmax(mission):
    from aircraft_foundry.core.evaluator import evaluate
    small = AircraftParameters()
    small.controls.flap_end = 0.4
    big = AircraftParameters()
    assert evaluate(big, mission).metrics["clmax_landing"] > evaluate(small, mission).metrics["clmax_landing"]
