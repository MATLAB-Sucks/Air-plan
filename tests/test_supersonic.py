"""Supersonic aerodynamics, sonic boom and noise controls."""
import numpy as np
import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, baseline, configurations_for
from aircraft_foundry.numerics import noise as nz
from aircraft_foundry.numerics import sonic_boom as sb
from aircraft_foundry.numerics.propulsion import thrust_lapse, tsfc_per_hour
from aircraft_foundry.numerics.supersonic import area_rule_wave_drag_area, sears_haack_wave_drag_area

CONCORDE_MISSION = MissionRequirements(name="Concorde-like", payload_kg=12000, range_km=6500, cruise_mach=2.0,
                                       cruise_altitude_m=17000, takeoff_field_m=3600, max_approach_speed_ms=85,
                                       max_span_m=30, max_flyover_db=125, max_cabin_dba=88)


def test_area_rule_reproduces_sears_haack():
    x = np.linspace(0, 60, 801)
    s = 9.0 * (1 - (2 * x / 60 - 1) ** 2) ** 1.5
    assert area_rule_wave_drag_area(x, s) == pytest.approx(sears_haack_wave_drag_area(9.0, 60), rel=0.02)


def test_ogive_nose_lowers_wave_drag():
    from aircraft_foundry.geometry.fuselage import FuselageShape
    x = np.linspace(0, 62, 240)
    d = {}
    for prof in ("elliptic", "ogive"):
        f = FuselageShape(62, 2.9, 1.0, 0.17, 0.2, 0.05, nose_profile=prof)
        d[prof] = area_rule_wave_drag_area(x, np.pi * f.half_width(x) * f.half_height(x))
    assert d["ogive"] < 0.75 * d["elliptic"]


def test_mach_cutoff_standard_atmosphere():
    assert sb.mach_cutoff(12000) == pytest.approx(1.15, abs=0.01)
    x = np.linspace(0, 30, 200)
    ae = 1.0 * np.clip(np.sin(np.pi * x / 30), 0, 1)
    assert not sb.n_wave(x, ae, 1.12, 12000).reaches_ground  # boomless below Mach cutoff
    assert sb.n_wave(x, ae, 1.4, 12000).overpressure_psf > 0


def test_boom_scaling_laws():
    x = np.linspace(0, 60, 300)
    vol = 7.0 * np.clip(np.sin(np.pi * x / 60), 0, 1) ** 1.2
    lift = np.where((x > 18) & (x < 56), 1.0, 0.0)

    def dp(weight, alt):
        lw = lift * weight * 9.81 / np.trapezoid(lift, x)
        return sb.n_wave(x, vol + sb.lift_equivalent_area(x, lw, 2.0, alt), 2.0, alt).overpressure_psf

    assert dp(150e3, 17000) > dp(100e3, 17000)  # heavier -> louder
    assert dp(150e3, 17000) < dp(150e3, 12000)  # higher -> quieter


def test_concorde_mission_sizes_to_a_feasible_concorde_class_design():
    p = baseline(CONCORDE_MISSION, configurations_for(CONCORDE_MISSION)[0])
    ev = evaluate(p, CONCORDE_MISSION)
    assert ev.feasible, ev.message
    m = ev.metrics
    assert 160e3 < m["mtow_kg"] < 240e3  # real Concorde 185 t
    assert 1.5 < m["boom_overpressure_psf"] < 2.6  # real ~1.9-2 psf
    assert 98 < m["boom_pldb"] < 110  # real ~105 PLdB
    assert p.wing.sweep_le_deg > 50 and p.wing.tc_root <= 0.035


def test_supersonic_cabins_are_slim():
    assert CONCORDE_MISSION.seats_abreast == 4
    assert MissionRequirements(payload_kg=12000).seats_abreast > 4


def test_overland_boom_limit_selects_boomless_cruise():
    m = MissionRequirements(**{**CONCORDE_MISSION.to_dict(), "overland_fraction": 0.5, "max_boom_pldb": 75})
    p = baseline(CONCORDE_MISSION, configurations_for(CONCORDE_MISSION)[0])
    ev = evaluate(p, m)
    mc = sb.mach_cutoff(m.cruise_altitude_m)
    assert ev.metrics["overland_mach"] < mc  # too loud for 75 PLdB: flies below Mach cutoff over land
    assert any("Mach cutoff" in n or "Mach 0.90" in n for n in ev.notes)
    loose = MissionRequirements(**{**m.to_dict(), "max_boom_pldb": 110})
    assert evaluate(p, loose).metrics["overland_mach"] == pytest.approx(2.0)
    assert evaluate(p, loose).metrics["trip_time_h"] < ev.metrics["trip_time_h"]


def test_engine_model_trends():
    assert tsfc_per_hour(0.8, "turbofan", 10) < tsfc_per_hour(0.8, "turbofan", 1)
    assert thrust_lapse(0.12, 2.0, "turbofan", 0.0) > thrust_lapse(0.12, 2.0, "turbofan", 5.0)
    assert thrust_lapse(0.12, 2.0, "turbofan", 0.0) == pytest.approx(0.4, abs=0.05)  # Olympus ~1/3


def test_noise_controls_trade_against_weight():
    # quieter airport: higher bypass ratio
    assert nz.flyover_level_db(2, 120e3, 8, 700) < nz.flyover_level_db(2, 120e3, 1, 700)
    # quieter cabin needs more insulation
    assert nz.insulation_needed_kg_m2(75, 9600, 100) > nz.insulation_needed_kg_m2(85, 9600, 100)
    quiet = MissionRequirements(max_cabin_dba=74.0)
    normal = MissionRequirements()
    cfg = _C("turbofan", "wing", "conventional", "low")
    pq, pn = baseline(quiet, cfg), baseline(normal, cfg)
    eq, en = evaluate(pq, quiet), evaluate(pn, normal)
    assert pq.structure.insulation_kg_m2 > pn.structure.insulation_kg_m2
    assert eq.weights["acoustic_insulation"] > en.weights["acoustic_insulation"]
    assert eq.metrics["cabin_noise_dba"] <= 74.0 + 0.5


def test_subsonic_noise_calibration():
    m = MissionRequirements()
    ev = evaluate(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    assert 80 < ev.metrics["flyover_noise_db"] < 90  # A320-class ~85
    assert 76 < ev.metrics["cabin_noise_dba"] <= 82.5


def test_transonic_cruise_rejected():
    assert MissionRequirements(cruise_mach=1.0).validate()
