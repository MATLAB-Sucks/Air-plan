import numpy as np
import pytest

from aircraft_foundry.core.components import ComponentType as CT, Relation
from aircraft_foundry.core.design import AircraftDesign
from aircraft_foundry.core.parameters import AircraftParameters
from aircraft_foundry.geometry.loft import loft_to_mesh


CONFIGS = [
    {},
    {"configuration.engine_layout": "rear_fuselage", "configuration.tail_type": "t_tail"},
    {"configuration.propulsion": "turboprop", "configuration.wing_position": "high", "engines.thrust_kn": 40},
    {"engines.count": 4},
]


def make(mods):
    p = AircraftParameters()
    for k, v in mods.items():
        p.set(k, v)
    return AircraftDesign(p)


@pytest.mark.parametrize("mods", CONFIGS)
def test_every_component_is_a_closed_solid(mods):
    d = make(mods)
    for c in d.components.values():
        for lo in c.lofts:
            m = loft_to_mesh(lo)
            assert m.is_closed_manifold(), c.id
            assert m.signed_volume() > 0, c.id


@pytest.mark.parametrize("mods", CONFIGS)
def test_relationships_resolve(mods):
    d = make(mods)
    assert d.dangling_connections() == []
    for c in d.components.values():
        assert c.parent is None or c.parent in d.components


def test_component_identity_and_types():
    d = make({})
    types = {c.type for c in d.components.values()}
    assert {CT.FUSELAGE, CT.WING, CT.RIB, CT.SPAR, CT.FRAME, CT.LONGERON, CT.NACELLE, CT.WHEEL} <= types
    rib = d.component("wing.right.rib.03")
    assert rib.parent == "wing.right"
    rels = {(k.relation, k.target) for k in rib.connections}
    assert (Relation.BOUNDED_BY, "wing.right.spar.front") in rels
    assert (Relation.BOUNDED_BY, "wing.right.spar.rear") in rels
    assert "eta" in rib.parameters


def test_ribs_follow_span_and_pitch():
    d = make({})
    n0 = len(d.of_type(CT.RIB))
    d.set_parameter("wing.span", 44.0)
    ribs = [c for c in d.components.values() if c.id.startswith("wing.right.rib.")]
    assert len(d.of_type(CT.RIB)) > n0
    max_y = max(c.world_mesh().vertices[:, 1].max() for c in ribs)
    # outermost rib face lies on the tip plane (semi-span 22 m), none beyond it
    assert 20.5 < max_y <= 22.0 + 1e-9


def test_wing_edit_regenerates_only_dependents():
    d = make({})
    fus_fp = {cid: c.fingerprint for cid, c in d.components.items() if cid.startswith("fuselage")}
    cs = d.set_parameter("wing.sweep_le_deg", 30.0)
    # generators that may depend on the wing re-run (fins can sit on wing tips, booms on the wing) ...
    assert {"wing", "wing_structure", "wing_attachments", "engines", "gear", "control_surfaces"} <= set(
        cs.generators_run)
    assert "fuselage" not in cs.generators_run and "fuselage_structure" not in cs.generators_run
    # ... but on a conventional aircraft the tail geometry itself does not change
    assert not any(cid.startswith(("fuselage", "vtail", "htail", "attach.vtail", "attach.htail"))
                   for cid in cs.touched)
    assert all(d.component(cid).fingerprint == fp for cid, fp in fus_fp.items())
    assert "nacelle.1" in cs.updated  # engines are mounted on the wing


def test_attachments_move_with_wing():
    d = make({})
    before = np.array(d.component("attach.wing.right.front").placement.position)
    pylon_before = d.component("pylon.1").world_mesh().vertices.mean(0)
    d.set_parameter("wing.x_position", 0.42)
    after = np.array(d.component("attach.wing.right.front").placement.position)
    dx = (0.42 - 0.36) * d.params.fuselage.length
    assert after[0] - before[0] == pytest.approx(dx, abs=0.05)
    assert d.component("pylon.1").world_mesh().vertices.mean(0)[0] - pylon_before[0] == pytest.approx(dx, abs=0.05)


def test_attachment_references_nearest_frame_and_updates():
    d = make({})
    fitting = d.component("attach.wing.right.front")
    frame = fitting.parameters["frame"]
    assert frame in d.components
    d.set_parameter("fuselage.frame_pitch", 0.5)
    assert d.component("attach.wing.right.front").parameters["frame"] in d.components


def test_no_regeneration_when_value_unchanged():
    d = make({})
    cs = d.set_parameter("wing.span", d.params.wing.span)
    assert not cs and cs.unchanged == len(d.components)


def test_parameter_validation():
    p = AircraftParameters()
    with pytest.raises(ValueError):
        p.set("wing.span", 1000)
    with pytest.raises(ValueError):
        p.set("configuration.tail_type", "x_tail")  # not a supported tail
    p.set("fuselage.longerons", 9.6)
    assert p.fuselage.longerons == 10


def test_design_roundtrip_dict():
    d = make({"wing.span": 30})
    d2 = AircraftDesign.from_dict(d.to_dict())
    assert d2.params == d.params
    assert {c: x.fingerprint for c, x in d2.components.items()} == {c: x.fingerprint for c, x in d.components.items()}
