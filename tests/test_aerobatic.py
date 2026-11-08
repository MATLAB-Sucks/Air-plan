"""Stunt planes: light-aircraft weights, +-10 g structure, big controls, roll rate, nose propeller."""
import math

import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, aerobatic_mission, baseline, configurations_for
from aircraft_foundry.core.verdict import flight_verdict
from aircraft_foundry.geometry.arrangement import Arrangement
from aircraft_foundry.simulator.flightmodel import FlightSimulator, build_aero_model


@pytest.fixture(scope="module")
def stunt():
    m = aerobatic_mission()
    p = baseline(m, configurations_for(m)[0])
    return p, m, evaluate(p, m)


def test_stunt_plane_sizes_like_an_extra_330(stunt):
    p, m, ev = stunt
    assert ev.feasible, [c.name for c in ev.violated]
    mm = ev.metrics
    assert 800 < mm["mtow_kg"] < 1300  # Extra 330LX: 950 kg
    assert 7.0 < p.wing.span < 9.0  # 8.0 m
    assert p.structure.limit_load_factor == 10.0 and p.wing.camber == 0.0
    assert mm["roll_rate_deg_s"] > 300  # Extra 330: ~400 deg/s
    assert mm["static_tw"] > 0.5
    assert flight_verdict(p, m, ev).flies


def test_nose_propeller_no_flaps_full_span_ailerons(stunt):
    p, _, _ = stunt
    arr = Arrangement(p)
    assert len(arr.engines) == 1 and arr.engines[0].prop_x < 0.5  # propeller ahead of the nose
    kinds = [c.kind for c in arr.control_surfaces]
    assert "flap" not in kinds
    ail = [c for c in arr.control_surfaces if c.kind == "aileron"][0]
    assert ail.eta1 - ail.eta0 > 0.6 and ail.chord_fraction >= 0.3


def test_light_aircraft_weights_replace_transport_statistics(stunt):
    p, m, ev = stunt
    w = ev.weights
    assert w["fuselage"] < 250 and w["systems_and_equipment"] < 300  # transport equations gave 708 / 1277 kg
    assert w["acoustic_insulation"] == 0.0
    airliner = MissionRequirements()
    ev_a = evaluate(baseline(airliner, _C("turbofan", "wing", "conventional", "low")), airliner)
    assert 70000 < ev_a.metrics["mtow_kg"] < 95000  # airliners unchanged


def test_g_limit_now_drives_structural_weight(stunt):
    p, m, _ = stunt
    soft = p.copy()
    soft.set("structure.limit_load_factor", 4.4)
    w10, w44 = evaluate(p, m).weights["wing"], evaluate(soft, m).weights["wing"]
    assert w10 > 1.3 * w44  # a +-10 g wing is much heavier than a +4.4 g one


def test_roll_rate_and_symmetric_g_in_the_simulator(stunt):
    p, m, ev = stunt
    md = build_aero_model(p, m, ev)
    assert md.n_limit == 10.0 and md.n_negative_limit == 10.0
    sim = FlightSimulator(md)
    sim.start_in_flight(75, 1500)
    sim.controls.aileron, sim.controls.sas = 1.0, False
    for _ in range(100):
        sim.step(1 / 100)
    rate = math.degrees(abs(sim.state[10]))
    expected = ev.metrics["roll_rate_deg_s"] * 75 / ev.metrics["manoeuvring_speed_ms"]
    assert rate == pytest.approx(expected, rel=0.25)
    push = FlightSimulator(md)
    push.start_in_flight(75, 1500)
    push.controls.elevator, push.controls.sas = -1.0, False
    gmin = 0.0
    for _ in range(300):
        push.step(1 / 100)
        gmin = min(gmin, push.telemetry()["load_factor"])
    assert gmin < -3.0 and not push.failure  # inverted-capable: negative g well beyond a transport's -1 g


def test_airliner_still_breaks_at_minus_one_point_five_g():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    md = build_aero_model(p, m, evaluate(p, m))
    assert md.n_negative_limit == 1.0
