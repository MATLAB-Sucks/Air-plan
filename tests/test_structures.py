import numpy as np
import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.structures import farrar_allowable, gauge
from aircraft_foundry.numerics.materials import MATERIALS


def test_gauge_rounds_up():
    assert gauge(np.array([0.0009, 0.001, 0.0011]))[2] == pytest.approx(0.0012)
    assert gauge(np.array([0.001]))[0] == pytest.approx(0.001)


def test_farrar_closer_ribs_raise_allowable():
    m = MATERIALS["Al 7075-T6"]
    assert farrar_allowable(np.array([1e6]), m, 0.5)[0] > farrar_allowable(np.array([1e6]), m, 1.0)[0]
    assert farrar_allowable(np.array([1e12]), m, 0.5)[0] == pytest.approx(m.fcy)  # capped at yield


def test_wing_loads_physics(baseline_params, mission):
    st = evaluate(baseline_params, mission).structure
    w = st.wing
    assert np.all(np.diff(w.M) <= 1e-6)  # bending decreases monotonically to the tip
    assert w.M[-1] == pytest.approx(0, abs=1) and w.V[-1] == pytest.approx(0, abs=1)
    assert np.all(w.g_upper >= w.t_upper - 1e-12)  # gauges never below requirement
    assert np.all(np.minimum.reduce([w.ms_upper, w.ms_lower, w.ms_web]) >= -1e-9)
    assert 5e6 < w.M[0] < 15e6  # A320-class ultimate root bending, N m


def test_inertia_relief_reduces_root_bending(baseline_params, mission):
    from aircraft_foundry.core.structures import StructuralModel
    sm = StructuralModel(baseline_params, mission.cruise_altitude_m, mission.cruise_mach)
    with_fuel = sm.wing(80000, 20000, 6000, 3500)
    no_relief = sm.wing(80000, 20000, 0.0, 0.0)
    assert with_fuel.M[0] < no_relief.M[0]


def test_higher_load_factor_heavier_wing(baseline_params, mission):
    p = baseline_params.copy()
    p.structure.limit_load_factor = 3.8
    assert (evaluate(p, mission).metrics["wing_mass_structural_kg"]
            > evaluate(baseline_params, mission).metrics["wing_mass_structural_kg"])


def test_composite_wing_lighter(baseline_params, mission):
    p = baseline_params.copy()
    p.structure.wing_material = "CFRP quasi-isotropic"
    assert (evaluate(p, mission).metrics["wing_mass_structural_kg"]
            < evaluate(baseline_params, mission).metrics["wing_mass_structural_kg"])


def test_pressurisation_hoop_stress(baseline_params, mission):
    st = evaluate(baseline_params, mission).structure
    assert 45 < st.summary["cabin_dp_kPa"] < 65
    assert st.summary["max_hoop_stress_MPa"] <= MATERIALS["Al 2024-T3"].fatigue_hoop / 1e6 + 1e-6
    low = mission.__class__(**{**mission.to_dict(), "cruise_altitude_m": 2000.0})
    assert evaluate(baseline_params, low).structure.summary["cabin_dp_kPa"] == 0


def test_structural_and_statistical_weights_agree(baseline_params, mission):
    m = evaluate(baseline_params, mission).metrics
    assert 0.75 < m["wing_mass_structural_kg"] / m["wing_mass_statistical_kg"] < 1.3
    assert 0.75 < m["fuselage_mass_structural_kg"] / m["fuselage_mass_statistical_kg"] < 1.3


def test_structural_weight_method_closes(baseline_params, mission):
    p = baseline_params.copy()
    p.structure.weight_method = "structural"
    ev = evaluate(p, mission)
    assert ev.converged
    assert ev.weights["wing"] == pytest.approx(ev.metrics["wing_mass_structural_kg"], rel=0.02)


def test_member_results_reference_real_components(baseline_params, mission):
    from aircraft_foundry.core.design import AircraftDesign
    d = AircraftDesign(baseline_params)
    st = evaluate(baseline_params, mission).structure
    missing = [cid for cid in st.member_results if cid not in d.components]
    assert missing == []
    assert "wing.right.rib.03" in st.utilisation and "fuselage.frame.10" in st.utilisation
    assert st.member_results["vtail.rudder"]["hinge_moment_kNm"] > 0
