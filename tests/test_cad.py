import pytest

cq = pytest.importorskip("cadquery")

from aircraft_foundry.core.design import AircraftDesign  # noqa: E402
from aircraft_foundry.core.parameters import AircraftParameters  # noqa: E402
from aircraft_foundry.fileio.cad_export import export_step_assembly, export_step_each  # noqa: E402
from aircraft_foundry.geometry.cad import component_shape  # noqa: E402

IDS = ["fuselage", "wing.right", "wing.left", "htail.right", "vtail", "nacelle.1", "pylon.1",
       "fuselage.frame.05", "wing.right.rib.04", "wing.right.spar.rear", "fuselage.longeron.01",
       "gear.main_right.wheel.0", "attach.wing.right.front"]


@pytest.mark.parametrize("mods", [{}, {"wing.twist_tip_deg": -6, "wing.camber": 0.05, "wing.tc_root": 0.2,
                                       "wing.tc_tip": 0.07, "wing.sweep_le_deg": 40}])
def test_solids_valid_and_match_mesh(mods):
    p = AircraftParameters()
    for k, v in mods.items():
        p.set(k, v)
    d = AircraftDesign(p)
    for cid in IDS:
        c = d.component(cid)
        s = component_shape(c)
        assert s.isValid(), cid
        ratio = s.Volume() / c.world_mesh().signed_volume()
        assert 0.95 < ratio < 1.06, (cid, ratio)
        bb = s.BoundingBox()
        lo, hi = c.world_mesh().bounds()
        assert abs(bb.xmin - lo[0]) < 0.05 and abs(bb.zmax - hi[2]) < 0.05, cid


@pytest.mark.parametrize("deflect", [0.0, 30.0])
def test_control_surface_solids_valid(deflect):
    p = AircraftParameters()
    for k in ("flap", "aileron", "elevator", "rudder"):
        setattr(p.controls, f"{k}_deflection_deg", deflect if k != "aileron" else -deflect / 2)
    d = AircraftDesign(p)
    for cid in ("wing.right", "wing.right.flap", "wing.left.aileron", "htail.left.elevator", "vtail.rudder", "vtail"):
        c = d.component(cid)
        s = component_shape(c)
        assert s.isValid(), cid
        assert s.Volume() / c.world_mesh().signed_volume() == pytest.approx(1.0, abs=0.03), cid


def test_turboprop_propeller_solid():
    p = AircraftParameters()
    p.set("configuration.propulsion", "turboprop")
    p.set("engines.thrust_kn", 40)
    assert component_shape(AircraftDesign(p).component("propeller.1")).isValid()


def test_step_assembly_roundtrip(params, tmp_path):
    d = AircraftDesign(params)
    comps = [d.component(i) for i in ("wing.right", "wing.right.rib.02", "fuselage.frame.03", "nacelle.1")]
    path = export_step_assembly(comps, tmp_path / "a.step", "test")
    text = path.read_text(errors="ignore")
    assert "ISO-10303-21" in text and "wing.right.rib.02" in text
    shape = cq.importers.importStep(str(path))
    expected = sum(component_shape(c).Volume() for c in comps)
    assert sum(s.Volume() for s in shape.solids().vals()) == pytest.approx(expected, rel=1e-3)


def test_step_per_component(params, tmp_path):
    d = AircraftDesign(params)
    files = export_step_each([d.component("vtail"), d.component("vtail.spar.front")], tmp_path)
    assert [f.name for f in files] == ["vtail.step", "vtail.spar.front.step"]


@pytest.mark.parametrize("layout", ["flying_wing", "blended_wing_body", "canard", "twin_boom", "v_tail"])
def test_unconventional_solids_valid(layout):
    """Every skin, control surface, boom and propeller of each layout is a valid B-rep matching its mesh."""
    from aircraft_foundry.core.synthesis import baseline
    mission, cfg = _case(layout)
    d = AircraftDesign(baseline(mission, cfg))
    for c in d.components.values():
        if c.is_structure:
            continue
        s = component_shape(c)
        assert s.isValid(), c.id
        assert s.Volume() / c.world_mesh().signed_volume() == pytest.approx(1.0, abs=0.06), c.id


def _case(layout):
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C
    two = MissionRequirements(payload_kg=200, range_km=7000, cruise_mach=0.85, cruise_altitude_m=12500,
                              takeoff_field_m=1800, max_approach_speed_ms=65, max_span_m=30)
    reg = MissionRequirements(payload_kg=4000, range_km=1200, cruise_mach=0.5, cruise_altitude_m=6000,
                              takeoff_field_m=1200, max_approach_speed_ms=58, max_span_m=28)
    wide = MissionRequirements(payload_kg=30000, range_km=9000, cruise_mach=0.84, cruise_altitude_m=11500,
                               takeoff_field_m=3000, max_approach_speed_ms=75, max_span_m=65)
    return {"flying_wing": (two, _C("turbofan", "upper_aft", "none", "mid", "flying_wing")),
            "blended_wing_body": (wide, _C("turbofan", "upper_aft", "twin_fin", "mid", "blended_wing_body")),
            "canard": (two, _C("turbofan", "rear_fuselage", "twin_fin", "low", "canard")),
            "twin_boom": (reg, _C("turboprop", "pusher", "twin_fin", "high", "twin_boom")),
            "v_tail": (two, _C("turbofan", "pusher", "v_tail", "low"))}[layout]
