import math

import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.generator import EvolutionSettings, evolve, generate_candidates
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import configurations_for
from aircraft_foundry.numerics.atmosphere import isa


def test_isa_reference_values():
    sl, tropo = isa(0), isa(11000)
    assert sl.density == pytest.approx(1.225, rel=1e-3)
    assert tropo.temperature == pytest.approx(216.65, rel=1e-4)
    assert tropo.pressure == pytest.approx(22632, rel=2e-3)


def test_baseline_is_feasible_and_plausible(baseline_params, mission):
    ev = evaluate(baseline_params, mission)
    assert ev.feasible, ev.message
    m = ev.metrics
    # A320-class mission: published MTOW 73-79 t; conceptual methods within ~15 %
    assert 70000 < m["mtow_kg"] < 95000
    assert 0.48 < m["empty_fraction"] < 0.62
    assert 13 < m["cruise_ld"] < 19
    assert 0.05 <= m["static_margin"] <= 0.30


def test_weight_closure_is_consistent(baseline_params, mission):
    ev = evaluate(baseline_params, mission)
    total = sum(ev.weights.values())
    assert total == pytest.approx(ev.metrics["mtow_kg"], abs=2.0)


def test_constraints_detect_violation(baseline_params, mission):
    p = baseline_params.copy()
    p.wing.span = mission.max_span_m + 3
    ev = evaluate(p, mission)
    assert not ev.feasible
    assert "Wingspan" in [c.name for c in ev.violated]
    assert ev.score > evaluate(baseline_params, mission).score


def test_non_closing_design_is_reported(baseline_params, mission):
    p = baseline_params.copy()
    p.wing.span, p.wing.root_chord = 6.0, 0.8  # tiny wing for an airliner
    ev = evaluate(p, mission)
    assert not ev.feasible
    assert ev.score >= 1e6 or not ev.converged or ev.violated


def test_configurations_follow_mission():
    assert {c.propulsion for c in configurations_for(MissionRequirements())} == {"turbofan"}
    slow = MissionRequirements(cruise_mach=0.45, cruise_altitude_m=6000)
    assert {c.propulsion for c in configurations_for(slow)} == {"turboprop"}


def test_generation_is_deterministic_and_diverse(mission):
    a = generate_candidates(mission, count=5, seed=4, samples_per_config=6)
    b = generate_candidates(mission, count=5, seed=4, samples_per_config=6)
    assert [c.params for c in a] == [c.params for c in b]
    assert len({c.config_key for c in a}) == len(configurations_for(mission))
    assert any(c.evaluation.feasible for c in a)


def test_invalid_mission_rejected():
    with pytest.raises(ValueError):
        generate_candidates(MissionRequirements(propulsion="turboprop", cruise_mach=0.8), count=2)


def test_evolution_does_not_regress(mission):
    seeds = generate_candidates(mission, count=3, seed=2, samples_per_config=4)[:1]
    out = evolve(seeds, mission, EvolutionSettings(generations=4, population=8, seed=1))
    assert out and all(c.config_key == seeds[0].config_key for c in out)
    # the seed is re-derived inside evolution; the weight closure converges to 0.5 kg (brentq xtol),
    # so allow that solver tolerance, nothing more
    assert min(c.evaluation.score for c in out) <= seeds[0].evaluation.score * (1 + 1e-5)
    assert all(c.parents == [seeds[0].id] for c in out)


def test_evolution_cancel(mission):
    seeds = generate_candidates(mission, count=2, seed=2, samples_per_config=4)[:1]
    calls = []
    out = evolve(seeds, mission, EvolutionSettings(generations=50, population=8),
                 progress=lambda f, m: calls.append(f), cancel=lambda: len(calls) >= 2)
    assert len(calls) <= 3 and out
