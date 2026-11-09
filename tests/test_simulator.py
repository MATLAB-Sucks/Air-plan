"""Flight model: trim, dynamic modes, take-off, stability augmentation."""
import math

import pytest

from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.synthesis import _C, baseline
from aircraft_foundry.simulator.flightmodel import FlightSimulator, build_aero_model, dynamic_modes

TWO = MissionRequirements(payload_kg=200, range_km=7000, cruise_mach=0.85, cruise_altitude_m=12500,
                          takeoff_field_m=1800, max_approach_speed_ms=65, max_span_m=30)


@pytest.fixture(scope="module")
def airliner():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    ev = evaluate(p, m)
    return build_aero_model(p, m, ev), ev


@pytest.fixture(scope="module")
def flying_wing():
    p = baseline(TWO, _C("turbofan", "upper_aft", "none", "mid", "flying_wing"))
    ev = evaluate(p, TWO)
    return build_aero_model(p, TWO, ev), ev


def test_trim_and_hands_off_cruise(airliner):
    md, _ = airliner
    sim = FlightSimulator(md)
    assert sim.start_in_flight(230, 11000)
    for _ in range(200 * 10):
        sim.step(1 / 200)
    t = sim.telemetry()
    assert abs(t["altitude"] - 11000) < 30 and abs(t["airspeed"] - 230) < 3 and abs(t["roll"]) < 1


def test_airliner_modes_typical(airliner):
    md, _ = airliner
    modes = {mo.name: mo for mo in dynamic_modes(FlightSimulator(md), 230, 11000)}
    assert 0.6 < modes["Short period"].omega_n < 3 and modes["Short period"].ok
    assert 60 < modes["Phugoid"].period_s < 200
    assert modes["Dutch roll"].omega_n > 0.4
    assert modes["Roll subsidence"].time_const_s < 1.4


def test_takeoff_from_runway(airliner):
    md, ev = airliner
    sim = FlightSimulator(md)
    sim.place_on_runway()
    for _ in range(400):
        sim.step(1 / 200)
    assert sim.telemetry()["on_ground"] and not sim.crashed and abs(sim.telemetry()["pitch"]) < 2
    c = sim.controls
    c.brakes, c.throttle, c.flaps = False, 1.0, 0.4
    vr = 1.05 * ev.metrics["approach_speed_ms"] / 1.23 * 1.15
    roll = None
    for _ in range(200 * 90):
        t = sim.telemetry()
        c.elevator = (0.5 if t["pitch"] < 9 else -0.1) if t["airspeed"] > vr else 0.0
        sim.step(1 / 200)
        if roll is None and not t["on_ground"] and t["altitude"] > 3:
            roll = sim.state[0]
        if t["altitude"] > 300 or sim.crashed:
            break
    assert not sim.crashed and sim.telemetry()["altitude"] > 300
    assert roll < ev.metrics["takeoff_field_m"]  # ground roll shorter than the certified field length


def test_brakes_hold_and_hard_landing_detected(airliner):
    md, _ = airliner
    sim = FlightSimulator(md)
    sim.place_on_runway()
    sim.controls.throttle = 0.3
    for _ in range(200 * 5):
        sim.step(1 / 200)
    assert sim.telemetry()["ground_speed"] < 2.0  # brakes on
    sim.start_in_flight(110, 60)
    sim.controls.gear_down, sim.controls.throttle, sim.controls.pitch_trim = True, 0.0, 0.0
    sim.controls.elevator = -1.0  # full nose-down push into the ground
    for _ in range(200 * 15):
        sim.step(1 / 200)
        if sim.crashed:
            break
    assert sim.crashed


def _roll_input(md, sas):
    sim = FlightSimulator(md)
    sim.start_in_flight(130, 1500)
    sim.controls.sas = sas
    sim.controls.aileron = 0.2
    for i in range(200 * 20):
        if i == 200 * 5:
            sim.controls.aileron = 0.0
        sim.step(1 / 200)
    return sim.telemetry()


def test_flying_wing_needs_stability_augmentation(flying_wing):
    """B-2 behaviour: finless flying wings depart without fly-by-wire and fly normally with it."""
    md, _ = flying_wing
    assert md.Cnb < 0.02 and FlightSimulator(md).controls.sas  # augmentation on by default
    with_sas, without = _roll_input(md, True), _roll_input(md, False)
    assert abs(with_sas["beta"]) < 2 and with_sas["roll"] > 10  # coordinated right turn
    assert abs(without["beta"]) > 8  # large sideslip excursion: departure


def test_control_power_from_geometry(airliner):
    md, _ = airliner
    assert md.Cmde > 0 and md.Clda > 0 and md.Cndr > 0  # all control inputs act in the intended sense
    assert md.Cma < 0 and md.Cnb > 0 and md.Clp < 0  # statically stable, roll damped


# ---------------------------------------------------------------------------------- terrain, circuit, landing
from aircraft_foundry.simulator.terrain import RUNWAY, Terrain  # noqa: E402


def _vref(md):
    return 1.23 * math.sqrt(2 * md.mass * 9.80665 / (1.225 * md.S * (md.CLmax + md.flap_dCL)))


def fly_approach_and_land(sim, md, start_dist=6000.0):
    """A simple 'test pilot': 3 deg glide path, VREF, flare, idle, brakes."""
    aim = RUNWAY["north0"] + 300
    vref = _vref(md)
    north = aim - start_dist
    assert sim.start_in_flight(vref, start_dist * math.tan(3 * math.pi / 180), 0.0, north=north, flaps=1.0, gear=True)
    c = sim.controls
    c.sas = True
    thr_i = c.throttle
    dt = 1 / 200
    for _ in range(200 * 200):
        t = sim.telemetry()
        s = sim.state
        agl = sim.height_above_ground()
        V = t["airspeed"]
        gamma = math.degrees(math.asin(max(-1, min(1, t["vs"] / max(V, 1)))))
        if t["on_ground"]:
            c.throttle, c.brakes, c.elevator = 0.0, True, 0.0
            c.aileron = max(-1, min(1, -0.03 * s[1] - 0.05 * t["roll"]))
        else:
            dist = aim - s[0]
            h_gs = math.tan(3 * math.pi / 180) * max(dist, 0)
            gamma_cmd = -3.0 + max(-2, min(2, 0.05 * (h_gs - agl)))
            if agl < 20:  # flare from ~20 m: shallow the descent towards -0.5 deg, then close the throttle
                gamma_cmd = -3.0 + 2.5 * (1 - agl / 20)
                thr_i = 0.0 if agl < 5 else thr_i
            pitch_cmd = gamma_cmd + t["alpha"]
            c.elevator = max(-1, min(1, 0.12 * (pitch_cmd - t["pitch"]) - 2.0 * s[11]))
            if agl >= 5:
                thr_i = max(0.0, min(1.0, thr_i + 0.02 * (vref - V) * dt * 200 / 10))
            c.throttle = thr_i
            heading_cmd = -0.01 * s[1]  # radians: steer onto the centreline
            hdg = math.radians((t["heading"] + 180) % 360 - 180)
            roll_cmd = max(-15, min(15, 60 * (heading_cmd - hdg)))
            c.aileron = max(-1, min(1, 0.05 * (roll_cmd - t["roll"])))
        sim.step(dt)
        if sim.crashed or sim.landing:
            break
    return sim


def test_terrain_is_flat_at_the_airfield_and_low_on_approach():
    T = Terrain()
    for n in (RUNWAY["north0"], 0.0, 1500.0, RUNWAY["north1"]):
        assert T.surface_height(n, 0.0) == pytest.approx(0.0, abs=0.01)
    assert max(T.surface_height(-k * 1000.0, 0.0) for k in range(2, 16)) < 60  # approach corridor
    _, _, h = T.grid
    assert h.max() > 1000 and h.min() < -20  # mountains and a lake


def test_flight_into_terrain_is_a_crash(airliner):
    md, _ = airliner
    T = Terrain()
    sim = FlightSimulator(md, T)
    # fly level at 400 m straight at the north-eastern mountains
    sim.start_in_flight(150, 400, 40.0, north=8000, east=5000)
    for _ in range(200 * 240):
        sim.step(1 / 200)
        if sim.crashed:
            break
    assert "terrain" in sim.crashed or "ground" in sim.crashed


def test_wheels_roll_on_terrain_height(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    sim.place_on_runway()
    for _ in range(400):
        sim.step(1 / 200)
    assert sim.telemetry()["on_ground"] and not sim.crashed
    assert 0 < sim.height_above_ground() < 8


def test_approach_and_landing_is_flyable_and_rated(airliner):
    md, _ = airliner
    sim = fly_approach_and_land(FlightSimulator(md, Terrain()), md)
    assert not sim.crashed, sim.crashed
    assert sim.landing is not None
    L = sim.landing
    assert L["verdict"] == "Landed" and L["sink_ms"] < 3.0
    assert abs(L["centreline_offset_m"]) < RUNWAY["half_width"]
    assert 0 < L["score"] <= 100


def test_landing_rating_rules():
    from aircraft_foundry.simulator.flightmodel import rate_landing
    import numpy as np
    good = rate_landing({"sink_ms": 0.8, "north": 100.0, "east": 1.0, "roll_deg": 0.5}, np.array([1500.0, 0.0, 0]))
    hard = rate_landing({"sink_ms": 2.8, "north": 100.0, "east": 1.0, "roll_deg": 0.5}, np.array([1500.0, 0.0, 0]))
    off = rate_landing({"sink_ms": 0.8, "north": 100.0, "east": 60.0, "roll_deg": 0.5}, np.array([1500.0, 60.0, 0]))
    assert good["score"] > hard["score"] > off["score"]
    assert good["feel"] == "smooth" and hard["feel"] == "firm" and off["verdict"] == "Landed off the runway"


# ---------------------------------------------------------------------------------- world, fuel, structure, autopilot
from aircraft_foundry.simulator.autopilot import Autopilot  # noqa: E402
from aircraft_foundry.simulator.terrain import HOME, RUNWAYS, runway_by_code  # noqa: E402


def test_world_has_four_level_airports_with_clear_approaches():
    T = Terrain()
    assert len(RUNWAYS) == 4
    for rw in RUNWAYS:
        for a in (0.0, rw.length / 2, rw.length):
            assert T.surface_height(*rw.point(a)) == pytest.approx(rw.elevation, abs=0.01)
        for d in range(1000, 13000, 500):  # 3 deg approach keeps clearance over the terrain
            assert math.tan(math.radians(3)) * d - (T.surface_height(*rw.point(-d)) - rw.elevation) > 15
    _, _, h = T.grid
    assert h.max() > 1800 and h.min() < -60  # high mountains and a real sea


def test_one_way_runway_departs_away_from_terrain(airliner):
    md, _ = airliner
    hld = runway_by_code("HLD")
    assert not hld.approach_both_ends and hld.departure().heading_deg == pytest.approx(225.0)
    sim = FlightSimulator(md, Terrain())
    sim.place_on_runway(hld)
    assert sim.telemetry()["heading"] == pytest.approx(225.0, abs=0.5)
    assert sim.telemetry()["on_ground"] or sim.height_above_ground() < 8  # telemetry is fresh after placing


def test_fuel_burn_mass_and_flameout(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    sim.start_in_flight(230, 11000)
    f0, m0 = sim.fuel, sim.mass
    for _ in range(100 * 60):
        sim.step(1 / 100)
    t = sim.telemetry()
    assert 1500 < t["fuel_flow_kgh"] < 4000  # A320-class cruise ~2 400 kg/h
    assert sim.fuel < f0 and sim.mass < m0
    sim.fuel = 0.0
    sim.step(1 / 100)
    assert sim.last_forces["thrust"] == 0.0  # flame-out


def test_structural_failure_and_manoeuvring_speed(airliner):
    md, _ = airliner
    fast = FlightSimulator(md, Terrain())
    fast.start_in_flight(195, 3000)  # clearly above manoeuvring speed (the stall model rounds off CLmax)
    fast.controls.elevator, fast.controls.sas = 1.0, False
    for _ in range(100 * 10):
        fast.step(1 / 100)
        if fast.failure:
            break
    assert "wing" in fast.failure and "STRUCTURAL FAILURE" in fast.failure_msg
    slow = FlightSimulator(md, Terrain())  # below manoeuvring speed the wing stalls before it breaks
    slow.start_in_flight(140, 3000)
    slow.controls.elevator, slow.controls.sas = 1.0, False
    for _ in range(100 * 10):
        slow.step(1 / 100)
    assert not slow.failure


def test_rolling_pullup_loads_one_wing_more(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    sim.start_in_flight(200, 3000)
    sim.controls.elevator, sim.controls.aileron = 0.4, 1.0
    for _ in range(100 * 2):
        sim.step(1 / 100)
    assert sim.stress["left"] > 1.2 * sim.stress["right"]  # rolling right overloads the left wing


def _autopilot(md, sim):
    vref = 1.23 * math.sqrt(2 * sim.mass * 9.80665 / (1.225 * md.S * (md.CLmax + md.flap_dCL)))
    return Autopilot(sim, vref, climb_speed=1.6 * vref, cruise_speed=230.0)


def test_autopilot_lands_from_an_offset_intercept(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    n, e = HOME.point(-16000, 2500)
    sim.start_in_flight(130, 900, 330.0, north=n, east=e)
    ap = _autopilot(md, sim)
    ap.engage("land", destination=HOME)
    for _ in range(100 * 600):
        ap.update(1 / 100)
        sim.step(1 / 100)
        if not ap.engaged or sim.crashed:
            break
    assert not sim.crashed and sim.landing is not None
    assert sim.landing["verdict"] == "Landed" and sim.landing["score"] > 50


def test_autopilot_hold_and_manual_disconnect(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    sim.start_in_flight(200, 3000, 90.0)
    sim.controls.elevator = 0.2  # disturb
    ap = _autopilot(md, sim)
    ap.engage("hold")
    for _ in range(100 * 60):
        ap.update(1 / 100)
        sim.step(1 / 100)
    t = sim.telemetry()
    assert abs(t["altitude"] - 3000) < 60 and abs(((t["heading"] - 90) + 180) % 360 - 180) < 3
    ap.disengage("manual control input")
    assert not ap.engaged and "manual" in ap.status


def test_stall_protection_prevents_deep_stall(airliner):
    md, _ = airliner
    sim = FlightSimulator(md, Terrain())
    sim.start_in_flight(110, 1500)  # slow, then ask for a big climb at idle-ish target speed
    ap = _autopilot(md, sim)
    ap.engage("hold")
    ap._targets = {"alt": 2500.0, "hdg": 0.0, "spd": 95.0}
    worst = 0.0
    for _ in range(100 * 60):
        ap.update(1 / 100)
        sim.step(1 / 100)
        worst = max(worst, sim.telemetry()["alpha"])
    assert worst < 1.05 * math.degrees((md.CLmax - md.CL0) / md.CLa) and not sim.crashed


def test_real_world_lasham_loads_with_real_runways():
    from aircraft_foundry.simulator.world import available_worlds, load_world
    names = [n for n, _ in available_worlds()]
    assert "lasham" in names and "test-range" in names
    w = load_world("lasham")
    assert w.home.code.startswith("EGHL") and w.home.name.startswith("Lasham")
    assert 80 < w.home.heading_deg < 95 and 1500 < w.home.length < 1900  # Lasham 09/27, ~1.7-1.8 km
    assert 170 < w.home.elevation < 200  # 618 ft
    assert w.home.width >= 40  # the outline's real width, not a centre line's default
    codes = [r.code for r in w.runways]
    assert len(codes) == len(set(codes)) and sum(c.startswith("EGHL 09") for c in codes) == 1  # duplicates merged
    for r in w.runways:  # every runway sits on the terrain
        assert w.surface_height(*r.point(r.length / 2)) == pytest.approx(r.elevation, abs=0.5)
    assert any(r.name == "RAF Odiham" for r in w.runways) and w.texture_path is not None and w.attribution



def test_stall_spin_and_recovery():
    """Lift rounds off at CLmax, the nose drops; full rudder in a stall gives a spin that follows the
    rudder; the standard recovery (opposite rudder, stick forward) stops it."""
    from aircraft_foundry.core.synthesis import aerobatic_mission, configurations_for
    m = aerobatic_mission()
    p = baseline(m, configurations_for(m)[0])
    md = build_aero_model(p, m, evaluate(p, m))
    sim = FlightSimulator(md)
    sim.start_in_flight(45, 1500)
    sim.controls.throttle, sim.controls.sas = 0.0, False
    peak, stalled_pitch, later = 0.0, None, 99.0
    for i in range(100 * 20):
        sim.controls.elevator = min(1.0, i / 1200)
        sim.step(1 / 100)
        peak = max(peak, sim.last_forces["cl"])
        if stalled_pitch is None and sim.last_forces["stalled"]:
            stalled_pitch = sim.telemetry()["pitch"]
        elif stalled_pitch is not None:
            later = min(later, sim.telemetry()["pitch"])
    assert 0.9 * md.CLmax < peak < 1.05 * md.CLmax and stalled_pitch is not None and later < stalled_pitch - 15
    spin = FlightSimulator(md)
    spin.start_in_flight(40, 2000)
    spin.controls.throttle, spin.controls.sas = 0.0, False
    for _ in range(100 * 14):
        spin.controls.elevator, spin.controls.rudder = 1.0, 1.0
        spin.step(1 / 100)
    assert spin.last_forces["stalled"] and math.degrees(spin.state[12]) > 40 and math.degrees(spin.state[10]) > 0
    assert spin.telemetry()["altitude"] < 1800  # spins lose height fast
    for _ in range(100 * 12):
        spin.controls.rudder = -1.0 if spin.time < 18 else 0.0
        spin.controls.elevator = -0.6 if spin.time < 17 else 0.1
        spin.step(1 / 100)
    assert not spin.last_forces["stalled"] and abs(math.degrees(spin.state[12])) < 10 and not spin.crashed


def test_t_tail_deep_stall():
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "rear_fuselage", "t_tail", "low"))
    md = build_aero_model(p, m, evaluate(p, m))
    assert md.t_tail
    sim = FlightSimulator(md)
    sim.start_in_flight(85, 3000)
    sim.controls.throttle, sim.controls.sas = 0.0, False
    for _ in range(100 * 20):
        sim.controls.elevator = 1.0 if sim.time < 12 else -1.0
        sim.step(1 / 100)
    assert sim.last_forces["stalled"] and sim.telemetry()["alpha"] > 25  # stuck nose-high despite full push
