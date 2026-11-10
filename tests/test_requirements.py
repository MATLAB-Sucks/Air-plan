"""Selectable requirements, G rating, per-component strength."""
import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, baseline

CFG = _C("turbofan", "wing", "conventional", "low")


def test_ignored_requirements_are_reported_but_not_designed_for():
    strict = MissionRequirements(max_flyover_db=70, max_cabin_dba=70)
    ev = evaluate(baseline(strict, CFG), strict)
    assert not ev.feasible
    relaxed = MissionRequirements(max_flyover_db=70, max_cabin_dba=70, ignore="airport_noise,cabin_noise")
    p = baseline(relaxed, CFG)
    ev2 = evaluate(p, relaxed)
    assert ev2.feasible
    noise = [c for c in ev2.constraints if "noise" in c.name.lower()]
    assert noise and all(not c.enforced and not c.met for c in noise)  # still shown, not enforced
    assert p.structure.insulation_kg_m2 == pytest.approx(0.3)  # no insulation sized for the cabin limit
    assert any("Not designed around" in n for n in ev2.notes)


def test_boom_ignored_flies_full_speed_overland():
    m = MissionRequirements(cruise_mach=2.0, cruise_altitude_m=17000, payload_kg=12000, range_km=6500,
                            overland_fraction=0.5, max_span_m=30, takeoff_field_m=3600, max_approach_speed_ms=85,
                            max_boom_pldb=75, max_flyover_db=125, max_cabin_dba=88)
    from aircraft_foundry.core.synthesis import configurations_for
    p = baseline(m, configurations_for(m)[0])
    slow = evaluate(p, m).metrics["overland_mach"]
    m.ignore = "boom"
    fast = evaluate(p, m).metrics["overland_mach"]
    assert fast == pytest.approx(2.0) and fast > slow


def test_g_rating_sizes_the_structure():
    w = {}
    for g in (2.5, 4.0):
        m = MissionRequirements(g_limit=g)
        p = baseline(m, CFG)
        ev = evaluate(p, m)
        assert p.structure.limit_load_factor == g and ev.feasible
        w[g] = ev.weights["wing"]
    assert w[4.0] > 1.3 * w[2.5]


def test_component_strength_and_material():
    m = MissionRequirements()
    p = baseline(m, CFG)
    base = evaluate(p, m)
    p.set("structure.wing_strength", 2.0)
    strong = evaluate(p, m)
    assert strong.weights["wing"] > 1.3 * base.weights["wing"]
    assert strong.metrics["wing_failure_g"] == pytest.approx(2 * base.metrics["wing_failure_g"])
    p.set("structure.wing_strength", 1.0)
    p.set("structure.wing_material", "CFRP quasi-isotropic")
    assert evaluate(p, m).weights["wing"] < base.weights["wing"]


def test_stronger_wing_survives_more_g_in_the_simulator():
    from aircraft_foundry.simulator.flightmodel import FlightSimulator, build_aero_model
    m = MissionRequirements()

    def peak_before_failure(strength):
        p = baseline(m, CFG)
        p.set("structure.wing_strength", strength)
        sim = FlightSimulator(build_aero_model(p, m, evaluate(p, m)))
        sim.start_in_flight(205, 3000)  # well above manoeuvring speed
        sim.controls.elevator, sim.controls.sas = 1.0, False
        g = 0.0
        for _ in range(100 * 8):
            sim.step(1 / 100)
            g = max(g, sim.telemetry()["load_factor"])
            if sim.failure:
                return g, True
        return g, False
    g1, broke1 = peak_before_failure(1.0)
    g2, broke2 = peak_before_failure(1.6)
    assert broke1 and (not broke2 or g2 > g1 * 1.2)
