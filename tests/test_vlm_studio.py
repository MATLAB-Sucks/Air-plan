"""Vortex-lattice wind tunnel and the 'will it fly?' verdict."""
import math

import numpy as np
import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, baseline
from aircraft_foundry.core.verdict import flight_verdict
from aircraft_foundry.geometry.arrangement import Arrangement
from aircraft_foundry.numerics.vlm import VLMModel


@pytest.fixture(scope="module")
def airliner():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    ev = evaluate(p, m)
    return p, m, ev, VLMModel(Arrangement(p), ev.metrics["x_cg_m"])


def test_vlm_matches_lifting_surface_theory(airliner):
    p, _, _, vm = airliner
    r0, r4 = vm.solve(0.0), vm.solve(4.0)
    cla = (r4.CL - r0.CL) / math.radians(4)
    w = Arrangement(p).wing
    ar = w.span ** 2 / w.area
    helm = 2 * math.pi * ar / (2 + math.sqrt(4 + ar ** 2 * (1 + math.tan(w.sweep_at(0.5)) ** 2)))
    assert helm < cla < 1.35 * helm  # wing (Helmbold/DATCOM) plus the tail's contribution
    assert 0.9 < r4.span_eff <= 1.05  # near-elliptic loading
    assert r4.CDi == pytest.approx(r4.CL ** 2 / (math.pi * ar * r4.span_eff), rel=1e-6)


def test_vlm_controls_and_sideslip_signs(airliner):
    _, _, _, vm = airliner
    base = vm.solve(4.0)
    assert vm.solve(4.0, deflections={"elevator": -10}).Cm > base.Cm  # TE up -> nose up
    r = vm.solve(4.0, deflections={"aileron": 10})
    mids = 0.5 * (vm.pan.a + vm.pan.b)
    roll = np.cross(mids - [vm.x_cg, 0, 0], r.forces).sum(0)[0]
    assert roll < 0  # right roll is a negative moment about the aft-pointing x axis
    assert vm.solve(4.0, beta_deg=5).CY < 0  # wind from the right pushes the fin left


def test_vlm_stability_agrees_with_evaluator_sign(airliner):
    _, _, ev, vm = airliner
    r0, r4 = vm.solve(0.0), vm.solve(4.0)
    sm = -(r4.Cm - r0.Cm) / (r4.CL - r0.CL)
    assert sm > 0 and ev.metrics["static_margin"] > 0
    assert abs(sm - ev.metrics["static_margin"]) < 0.15


def test_flow_field_shows_downwash_and_tip_vortex(airliner):
    p, _, _, vm = airliner
    res = vm.solve(6.0)
    arr = Arrangement(p)
    wing = [c for c, s in zip(vm.pan.centroid, vm.pan.surface) if s == "wing"]
    wing = np.array(wing)
    tip = wing[np.argmax(wing[:, 1])]
    x_te = wing[:, 0].max()
    behind = vm.velocity(np.array([[x_te + 5, 0.3 * arr.wing.semi_span, tip[2]]]), res)[0]
    assert behind[2] < res.vinf[2]  # downwash behind the wing
    above = vm.velocity(np.array([[x_te + 3, tip[1] - 0.5, tip[2] + 1.0]]), res)[0]
    below = vm.velocity(np.array([[x_te + 3, tip[1] - 0.5, tip[2] - 1.0]]), res)[0]
    assert above[1] * below[1] < 0  # flow swirls around the tip vortex (opposite spanwise flow above/below)


def test_verdict_good_designs_fly():
    m = MissionRequirements()
    v = flight_verdict(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    assert v.flies and v.meets_mission and all(c.ok for c in v.checks)


def test_verdict_flying_wing_needs_augmentation():
    m = MissionRequirements(payload_kg=200, range_km=7000, cruise_mach=0.85, cruise_altitude_m=12500,
                            takeoff_field_m=1800, max_approach_speed_ms=65, max_span_m=30)
    v = flight_verdict(baseline(m, _C("turbofan", "upper_aft", "none", "mid", "flying_wing")), m)
    assert v.flies and any(c.warning for c in v.checks) and "augmentation" in v.headline


def test_verdict_rejects_a_tiny_tail():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    p.set("htail.span", 3.0)
    p.set("htail.root_chord", 1.0)
    v = flight_verdict(p, m)
    assert not v.flies
    failed = {c.name for c in v.checks if not c.ok}
    assert "Stable in pitch" in failed


def test_verdict_rejects_tiny_ailerons():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    p.set("controls.aileron_start", 0.9)
    p.set("controls.aileron_end", 0.95)
    p.set("controls.aileron_chord", 0.15)
    v = flight_verdict(p, m)
    assert "Roll control" in {c.name for c in v.checks if not c.ok}
