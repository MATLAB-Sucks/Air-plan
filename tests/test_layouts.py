"""Unconventional layouts: geometry, physics and generation."""
import math

import numpy as np
import pytest

from aircraft_foundry.core.components import ComponentType as CT
from aircraft_foundry.core.design import AircraftDesign
from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.generator import generate_candidates
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, baseline, configurations_for
from aircraft_foundry.geometry.arrangement import Arrangement
from aircraft_foundry.geometry.loft import loft_to_mesh
from aircraft_foundry.geometry.planform import LiftingSurface

TWO_SEAT = MissionRequirements(name="fast two-seater", payload_kg=200, range_km=7000, cruise_mach=0.85,
                               cruise_altitude_m=12500, takeoff_field_m=1800, max_approach_speed_ms=65, max_span_m=30)
REGIONAL = MissionRequirements(name="regional", payload_kg=4000, range_km=1200, cruise_mach=0.5,
                               cruise_altitude_m=6000, takeoff_field_m=1200, max_approach_speed_ms=58, max_span_m=28)
WIDEBODY = MissionRequirements(name="widebody", payload_kg=30000, range_km=9000, cruise_mach=0.84,
                               cruise_altitude_m=11500, takeoff_field_m=3000, max_approach_speed_ms=75, max_span_m=65)

CASES = {
    "v_tail": (TWO_SEAT, _C("turbofan", "pusher", "v_tail", "low")),
    "canard": (TWO_SEAT, _C("turbofan", "rear_fuselage", "twin_fin", "low", "canard")),
    "flying_wing": (TWO_SEAT, _C("turbofan", "upper_aft", "none", "mid", "flying_wing")),
    "twin_boom": (REGIONAL, _C("turboprop", "pusher", "twin_fin", "high", "twin_boom")),
    "h_tail": (REGIONAL, _C("turboprop", "wing", "twin_fin", "high")),
    "canard_pusher": (REGIONAL, _C("turboprop", "pusher", "twin_fin", "mid", "canard")),
    "bwb": (WIDEBODY, _C("turbofan", "upper_aft", "twin_fin", "mid", "blended_wing_body")),
}


@pytest.fixture(scope="module")
def sized():
    return {k: baseline(m, cfg) for k, (m, cfg) in CASES.items()}


def test_cranked_planform_degenerates_to_trapezoid():
    s = LiftingSurface(30, 6, 0.25, 30)
    c = LiftingSurface(30, 6, 0.25, 30, crank_eta=0.4, crank_chord_ratio=0.7, inner_sweep_le_deg=30)
    for k in ("area", "mac", "mac_eta", "x_mac_le"):
        assert getattr(c, k) == pytest.approx(getattr(s, k), rel=1e-6)
    assert c.sweep_at(0.25) == pytest.approx(s.sweep_at(0.25), rel=1e-6)


@pytest.mark.parametrize("name", list(CASES))
def test_layout_geometry_valid(sized, name):
    d = AircraftDesign(sized[name])
    assert d.dangling_connections() == []
    for c in d.components.values():
        for lo in c.lofts:
            m = loft_to_mesh(lo)
            assert m.is_closed_manifold() and m.signed_volume() > 0, c.id


@pytest.mark.parametrize("name", list(CASES))
def test_layout_closes(sized, name):
    m, _ = CASES[name]
    ev = evaluate(sized[name], m)
    assert ev.converged, ev.message
    assert ev.structure is not None


def test_layout_specific_parts(sized):
    types = {k: {c.type for c in AircraftDesign(p).components.values()} for k, p in sized.items()}
    assert CT.V_TAIL in types["v_tail"] and CT.RUDDERVATOR in types["v_tail"]
    assert CT.CANARD in types["canard"] and CT.HORIZONTAL_TAIL not in types["canard"]
    assert CT.ELEVON in types["flying_wing"] and CT.DRAG_RUDDER in types["flying_wing"]
    assert not {CT.HORIZONTAL_TAIL, CT.VERTICAL_TAIL, CT.FLAP} & types["flying_wing"]
    assert CT.BOOM in types["twin_boom"]
    assert CT.PROPELLER in types["twin_boom"]
    assert CT.ELEVON in types["bwb"] and CT.VERTICAL_TAIL in types["bwb"]


def test_twin_boom_tail_clears_pod_and_propellers(sized):
    arr = Arrangement(sized["twin_boom"])
    tail_le = arr.htail_placement.position[0]
    assert tail_le > arr.fuselage.length
    for e in arr.engines:
        assert e.pusher and e.prop_x < tail_le


def test_canard_ahead_of_wing_and_stable(sized):
    arr = Arrangement(sized["canard"])
    assert arr.placed("canard").x_ac < arr.x_ac_wing
    ev = evaluate(sized["canard"], TWO_SEAT)
    assert 0.05 <= ev.metrics["static_margin"] <= 0.30


def test_tailless_stability_and_notes(sized):
    ev = evaluate(sized["flying_wing"], TWO_SEAT)
    assert 0.02 <= ev.metrics["static_margin"] <= 0.15
    assert any("drag rudders" in n for n in ev.notes)
    names = {c.name for c in ev.constraints}
    assert "Tailless trim: tip washout" in names and "Horizontal tail volume" not in names


def test_bwb_cabin_fits_in_centrebody(sized):
    arr = Arrangement(sized["bwb"])
    assert arr.wing.cranked
    assert arr.centrebody_floor_area() >= WIDEBODY.cabin_floor_area_m2


def test_v_tail_area_split(sized):
    arr = Arrangement(sized["v_tail"])
    vt = arr.placed("vtail")
    gamma = math.radians(vt.surface.dihedral_deg)
    assert vt.horizontal_fraction == pytest.approx(math.cos(gamma) ** 2)
    assert vt.vertical_fraction + vt.horizontal_fraction == pytest.approx(1.0)


def test_two_seater_tail_in_proportion():
    """Regression: a long-range two-seater used to get a fin ~70 % of the fuselage length."""
    p = baseline(TWO_SEAT, _C("turbofan", "wing", "t_tail", "low"))
    arr = Arrangement(p)
    assert arr.vtail.semi_span / arr.fuselage.length <= 0.25
    assert arr.htail.area / arr.wing.area <= 0.40
    ev = evaluate(p, TWO_SEAT)
    assert ev.converged and not [c for c in ev.violated if "tail" in c.name.lower() or "Fin" in c.name]


def test_small_aircraft_crew_is_in_the_seats():
    assert TWO_SEAT.crew_kg == 0 and MissionRequirements().crew_kg > 0


def test_exploration_settings():
    all_ = {c.layout for c in configurations_for(TWO_SEAT)}
    assert {"conventional", "canard", "flying_wing"} <= all_
    unc = MissionRequirements(**{**TWO_SEAT.to_dict(), "explore": "unconventional only"})
    assert "conventional" not in {c.layout for c in configurations_for(unc)} or \
        all(c.tail_type == "v_tail" for c in configurations_for(unc) if c.layout == "conventional")
    conv = MissionRequirements(**{**TWO_SEAT.to_dict(), "explore": "conventional only"})
    assert {c.layout for c in configurations_for(conv)} == {"conventional"}


def test_generation_offers_several_layouts():
    cands = generate_candidates(TWO_SEAT, count=6, seed=3, samples_per_config=3)
    assert len({c.params.configuration.layout for c in cands}) >= 3


def test_flying_wing_beats_conventional_for_long_range_two_seater(sized):
    """Physics sanity: with no payload volume to carry, losing the tail and fuselage pays."""
    conv = evaluate(baseline(TWO_SEAT, _C("turbofan", "wing", "t_tail", "low")), TWO_SEAT)
    fw = evaluate(sized["flying_wing"], TWO_SEAT)
    assert fw.metrics["trip_fuel_kg"] < conv.metrics["trip_fuel_kg"]


def test_bwb_advantage_grows_with_size():
    """Literature trend: blended wing bodies only pay off at large size."""
    med = MissionRequirements()
    r_med = (evaluate(baseline(med, _C("turbofan", "upper_aft", "twin_fin", "mid", "blended_wing_body")), med)
             .metrics["fuel_per_pax_100km_kg"] /
             evaluate(baseline(med, _C("turbofan", "wing", "conventional", "low")), med).metrics["fuel_per_pax_100km_kg"])
    r_big = (evaluate(baseline(WIDEBODY, CASES["bwb"][1]), WIDEBODY).metrics["fuel_per_pax_100km_kg"] /
             evaluate(baseline(WIDEBODY, _C("turbofan", "wing", "conventional", "low")), WIDEBODY)
             .metrics["fuel_per_pax_100km_kg"])
    assert r_big < r_med and r_big < 1.0
