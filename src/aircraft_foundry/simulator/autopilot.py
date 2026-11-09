"""Autopilot and auto-throttle for the flight-test simulator.

Structure (as in real flight-control systems):

* inner loops - pitch-attitude hold (elevator, with rate damping and an
  integrator that finds the trim) and bank-angle hold (ailerons); the yaw
  damper / turn coordinator is the simulator's stability augmentation;
* outer loops - flight-path angle, altitude capture/hold, heading and
  waypoint tracking, localiser and 3 deg glide-path tracking, and an
  auto-throttle holding the target speed;
* a mode sequencer - TAKEOFF, NAV, APPROACH, FLARE, ROLLOUT, TOUCH-AND-GO.

Programs built on top:

* ``hold``      - keep the current altitude, heading and speed;
* ``circuit``   - take off, fly one circuit, land to a full stop;
* ``circuits``  - continuous touch-and-go circuits until the fuel reaches the
  reserve, then land.  Every lap is logged (fuel, distance, time, how well
  altitude and heading were held), so you can see how the aircraft behaves
  as it gets lighter;
* ``flight_plan`` - take off, fly user waypoints, approach and land at the
  destination runway.

Any manual control input disconnects the autopilot (handled by the window).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .terrain import HOME, Runway

D2R = math.pi / 180


@dataclass
class Waypoint:
    name: str
    north: float
    east: float
    altitude: float  # metres above sea level
    speed: float  # m/s true airspeed


@dataclass
class LapRecord:
    lap: int
    duration_s: float
    fuel_used_kg: float
    fuel_left_kg: float
    distance_km: float
    altitude_rms_m: float  # how well the downwind altitude was held
    bank_rms_deg: float
    landing: dict | None = None


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


@dataclass
class Autopilot:
    sim: object
    vref: float  # approach speed, m/s
    climb_speed: float
    cruise_speed: float
    program: str = "off"
    mode: str = "OFF"
    runway: Runway = HOME
    destination: Runway = HOME
    pattern_height: float = 450.0  # m above the field (~1 500 ft)
    reserve_fraction: float = 0.15
    plan: list[Waypoint] = field(default_factory=list)
    wp_index: int = 0
    status: str = "Autopilot off"
    laps: list[LapRecord] = field(default_factory=list)
    # internal controller state
    _pitch_i: float = 0.0
    _thr_i: float = 0.4
    _targets: dict = field(default_factory=dict)
    _lap_start: dict = field(default_factory=dict)
    _alt_err: list = field(default_factory=list)
    _bank_err: list = field(default_factory=list)
    _mode_time: float = 0.0
    _energy: int = 0

    # ------------------------------------------------------------------ engage / disengage
    def engage(self, program: str, runway: Runway | None = None, destination: Runway | None = None) -> None:
        sim = self.sim
        sim.derivative(sim.state, sim.controls)  # make sure telemetry reflects the current state
        t = sim.telemetry()
        self.program = program
        self.runway = runway or self._nearest_runway()
        on_rwy = t["on_ground"] or sim.height_above_ground() < 8
        if on_rwy and abs(wrap180(t["heading"] - self.runway.heading_deg)) > 90:
            self.runway = self.runway.reciprocal()  # take off in the direction the aircraft is lined up
        self.destination = destination or self.runway
        self.laps = []
        self._pitch_i = 0.0
        self._thr_i = float(sim.controls.throttle)
        self.wp_index = 0
        sim.controls.sas = True
        sim.controls.brakes = False
        if program == "hold":
            self._targets = {"alt": t["altitude"], "hdg": t["heading"], "spd": max(t["airspeed"], self.vref * 1.3)}
            self._set_mode("HOLD")
        elif t["on_ground"] or (sim.height_above_ground() < 8 and t["airspeed"] < 30):
            self._set_mode("TAKEOFF")
            self._start_lap()
        else:  # engaged in the air: go straight to the next stage
            if program == "flight_plan" and self.plan:
                self._set_mode("NAV")
            elif program == "land":
                self.plan = []  # straight to the destination's final approach fix
                self.wp_index = 0
                rw = self.destination
                along, right = rw.local(t["north"], t["east"])
                aligned = abs(wrap180(t["heading"] - rw.heading_deg)) < 50
                if -16000 < along < -2500 and abs(right) < 3000 and aligned:
                    self._set_mode("APPROACH")  # already in front of the runway: intercept directly
                else:
                    self._set_mode("NAV")
            else:
                self._build_pattern(self.runway)
                self._set_mode("NAV")
            self._start_lap()

    def disengage(self, reason: str = "") -> None:
        if self.program != "off":
            self.status = f"Autopilot disconnected{': ' + reason if reason else ''}"
        self.program, self.mode = "off", "OFF"

    @property
    def engaged(self) -> bool:
        return self.program != "off"

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._mode_time = self.sim.time
        if mode == "APPROACH":
            self._targets["established"] = False
            self._targets["gs"] = False

    def _nearest_runway(self) -> Runway:
        from .terrain import RUNWAYS
        RUNWAYS = getattr(self.sim.terrain, "runways", None) or RUNWAYS
        n, e = self.sim.state[0], self.sim.state[1]

        def dist(rw):
            a, r = rw.local(n, e)
            return abs(r) + max(0.0, -a, a - rw.length)
        return min(RUNWAYS, key=dist)

    # ------------------------------------------------------------------ routes
    def _build_pattern(self, rw: Runway) -> None:
        """Right-hand circuit: upwind, crosswind, downwind, base, final approach fix."""
        world_rws = getattr(self.sim.terrain, "runways", None)
        if world_rws:
            landing = next((r for r in world_rws if r.code == rw.code), rw)
        else:
            from .terrain import runway_by_code
            landing = runway_by_code(rw.code)
        if not landing.approach_both_ends:  # one-way strip: always land in its landing direction
            rw = landing
        h = rw.elevation + self.pattern_height
        off = 4500.0
        pts = [("upwind", rw.length + 2500, 0), ("crosswind", rw.length + 2500, off),
               ("downwind", rw.length / 2, off), ("downwind end", -8000, off), ("base", -13000, off * 0.5),
               ("final fix", -14500, 0)]
        pattern_speed = 1.45 * self.vref  # typical circuit speed (A320 ~200 kt)
        self.plan = [Waypoint(name, *rw.point(a, r), h if name != "final fix" else self._fix_altitude(rw),
                              pattern_speed) for name, a, r in pts]
        self.plan[-1].speed = self.vref * 1.25
        self.wp_index = 0
        self.destination = rw

    def _fix_altitude(self, rw: Runway) -> float:
        """Final-fix height: circuit height, never above the glide path - the glide slope is intercepted
        from below (as on a real ILS), so the aircraft never has to climb while slowing down."""
        return rw.elevation + min(self.pattern_height, math.tan(3 * D2R) * 14800)

    def approach_fix(self, rw: Runway) -> Waypoint:
        n, e = rw.point(-14500)
        return Waypoint("final fix", n, e, self._fix_altitude(rw), self.vref * 1.25)

    # ------------------------------------------------------------------ inner loops
    def _pitch_to(self, theta_cmd: float, dt: float) -> None:
        t, c = self.sim.telemetry(), self.sim.controls
        err = theta_cmd - t["pitch"]
        self._pitch_i = float(np.clip(self._pitch_i + 0.02 * err * dt, -0.6, 0.6))
        q = self.sim.state[11]
        c.elevator = float(np.clip(0.1 * err - 2.0 * q + self._pitch_i, -1, 1))

    def alpha_limit(self) -> float:
        """85 % of the stall angle of attack for the current flap setting (degrees)."""
        md, c = self.sim.m, self.sim.controls
        clmax = md.CLmax + md.flap_dCL * c.flaps
        return 0.85 * math.degrees((clmax - md.CL0 - md.flap_dCL * c.flaps) / md.CLa)

    def _gamma_to(self, gamma_cmd: float, dt: float) -> None:
        t = self.sim.telemetry()
        theta_cmd = gamma_cmd + t["alpha"]
        a_lim = self.alpha_limit()
        if t["alpha"] > a_lim:  # stall protection: lower the nose regardless of the path command
            theta_cmd = min(theta_cmd, t["pitch"] - 2.0 * (t["alpha"] - a_lim))
        self._pitch_to(theta_cmd, dt)

    def _bank_to(self, phi_cmd: float) -> None:
        t, c = self.sim.telemetry(), self.sim.controls
        p = self.sim.state[10] / D2R
        c.aileron = float(np.clip(0.05 * (phi_cmd - t["roll"]) - 0.02 * p, -1, 1))

    def _heading_to(self, hdg_cmd: float, max_bank: float = 25.0) -> None:
        t = self.sim.telemetry()
        self._bank_to(float(np.clip(1.5 * wrap180(hdg_cmd - t["heading"]), -max_bank, max_bank)))

    def _speed_to(self, v_cmd: float, dt: float) -> None:
        t, c = self.sim.telemetry(), self.sim.controls
        err = v_cmd - t["airspeed"]
        self._thr_i = float(np.clip(self._thr_i + 0.03 * err * dt, 0, 1))
        thr = self._thr_i + 0.05 * err
        # energy management: climb on climb power, descend near idle - pitch never fights the throttle
        lo, hi = (0.85, 1.0) if self._energy > 0 else ((0.0, 0.35) if self._energy < 0 else (0.0, 1.0))
        if t["alpha"] > self.alpha_limit():
            lo = 1.0  # stall protection also adds full power
        c.throttle = float(np.clip(thr, lo, hi))
        self._energy = 0

    def _altitude_to(self, alt_cmd: float, dt: float, max_climb: float = 8.0) -> None:
        t = self.sim.telemetry()
        err = alt_cmd - t["altitude"]
        self._gamma_to(float(np.clip(0.03 * err, -5.0, max_climb)), dt)
        self._energy = 1 if err > 60 else (-1 if err < -60 else 0)

    def _ground_steer(self, rw: Runway) -> None:
        t, c = self.sim.telemetry(), self.sim.controls
        _, right = rw.local(t["north"], t["east"])
        herr = wrap180(t["heading"] - rw.heading_deg)
        c.rudder = float(np.clip(-0.08 * right - 0.15 * herr, -1, 1))
        c.aileron = float(np.clip(-0.05 * t["roll"], -1, 1))

    # ------------------------------------------------------------------ lap logging
    def _start_lap(self) -> None:
        t = self.sim.telemetry()
        self._lap_start = {"time": self.sim.time, "fuel": t["fuel_kg"], "dist": t["distance_km"]}
        self._alt_err, self._bank_err = [], []

    def _end_lap(self, landing: dict | None) -> None:
        t = self.sim.telemetry()
        st = self._lap_start or {"time": 0, "fuel": t["fuel_kg"], "dist": 0}
        rms = (lambda v: float(np.sqrt(np.mean(np.square(v)))) if v else 0.0)
        self.laps.append(LapRecord(len(self.laps) + 1, self.sim.time - st["time"], st["fuel"] - t["fuel_kg"],
                                   t["fuel_kg"], t["distance_km"] - st["dist"], rms(self._alt_err),
                                   rms(self._bank_err), landing))
        self._start_lap()

    # ------------------------------------------------------------------ main update
    def update(self, dt: float) -> None:
        if not self.engaged or self.sim.crashed or self.sim.failure:
            if self.engaged and (self.sim.crashed or self.sim.failure):
                self.disengage("aircraft lost")
            return
        sim, c = self.sim, self.sim.controls
        t = sim.telemetry()
        agl = sim.height_above_ground()
        vs_clean = self.vref / 1.23 * 1.35
        if self.mode == "HOLD":
            g = self._targets
            self._altitude_to(g["alt"], dt)
            self._heading_to(g["hdg"])
            self._speed_to(g["spd"], dt)
            self.status = f"HOLD  alt {g['alt'] * 3.281:.0f} ft  hdg {g['hdg']:.0f}  spd {g['spd'] * 1.944:.0f} kt"
            return
        if self.mode == "TAKEOFF":
            rw = self.runway
            c.flaps, c.brakes = (0.4, False)
            c.throttle = 1.0
            self._thr_i = 1.0
            vr = self.vref / 1.23 * 1.1 * 1.05
            if t["on_ground"] and t["airspeed"] < vr:
                self._ground_steer(rw)
                c.elevator = 0.0
            else:
                self._pitch_to(min(8.0, 2.0 + 3.0 * (sim.time - self._mode_time)), dt)
                self._heading_to(rw.heading_deg, 10.0)
                c.rudder = 0.0
            if agl > 15:
                c.gear_down = False
            if agl > 150:
                c.flaps = 0.0 if t["airspeed"] > vs_clean else c.flaps
                if self.program == "flight_plan" and self.plan:
                    self._set_mode("NAV")
                else:
                    self._build_pattern(self.runway)
                    self._set_mode("NAV")
            self.status = f"TAKEOFF  {rw.code} rwy {rw.heading_deg:03.0f}  rotate at {vr * 1.944:.0f} kt"
            return
        if self.mode == "NAV":
            wp = self.plan[self.wp_index] if self.wp_index < len(self.plan) else self.approach_fix(self.destination)
            dn, de = wp.north - t["north"], wp.east - t["east"]
            dist = math.hypot(dn, de)
            bearing = math.degrees(math.atan2(de, dn)) % 360
            # terrain awareness: never descend below 300 m above the ground ahead
            ahead = [sim.ground(t["north"] + k * 1500 * math.cos(bearing * D2R),
                                t["east"] + k * 1500 * math.sin(bearing * D2R)) for k in range(1, 5)]
            alt_cmd = max(wp.altitude, max(ahead) + 300.0, sim.ground(t["north"], t["east"]) + 300.0)
            self._altitude_to(alt_cmd, dt)
            self._heading_to(bearing)
            self._speed_to(wp.speed, dt)
            c.gear_down = False
            if t["airspeed"] > vs_clean:
                c.flaps = 0.0
            if "downwind" in wp.name:
                self._alt_err.append(t["altitude"] - wp.altitude)
            self._bank_err.append(t["roll"] if "downwind" in wp.name else 0.0)
            # capture radius from the turn radius at this speed and 25 deg bank, so the aircraft can
            # actually turn onto the next leg instead of orbiting a waypoint it cannot reach
            turn_r = t["airspeed"] ** 2 / (9.81 * math.tan(25 * D2R))
            if dist < max(900.0, 1.1 * turn_r):
                self.wp_index += 1
                if self.wp_index > len(self.plan):
                    self._set_mode("APPROACH")
            self.status = (f"NAV  → {wp.name}  {dist / 1852:.1f} nm  brg {bearing:03.0f}  "
                           f"alt {alt_cmd * 3.281:.0f} ft  spd {wp.speed * 1.944:.0f} kt")
            return
        rw = self.destination
        along, right = rw.local(t["north"], t["east"])
        if self.mode == "APPROACH":
            dist_aim = rw.aim_point - along
            h_gs = rw.elevation + math.tan(3 * D2R) * max(dist_aim, 0.0)
            hagl_rw = t["altitude"] - rw.elevation
            # localiser: steer on the runway centreline using cross-track position and velocity (wind-proof)
            gs_ = t["ground_speed"]
            v_n, v_e = gs_ * math.cos(t["track"] * D2R), gs_ * math.sin(t["track"] * D2R)
            v_right = v_n * rw.perp[0] + v_e * rw.perp[1]
            course = rw.heading_deg - float(np.clip(0.05 * right + 0.8 * v_right, -30, 30))
            self._heading_to(course, 20.0)
            if abs(right) < 250 and abs(wrap180(t["track"] - rw.heading_deg)) < 20:
                self._targets["established"] = True
            established = self._targets.get("established", False)
            if dist_aim < 12000:  # configure early: gear down, landing flaps, slow to VREF
                c.gear_down, c.flaps = True, 1.0
            if established and t["altitude"] >= h_gs - 15:
                self._targets["gs"] = True  # glide slope captured (from below)
            if self._targets.get("gs"):
                gamma = -3.0 + float(np.clip(0.05 * (h_gs - t["altitude"]), -2.5, 3.0))
                self._gamma_to(gamma, dt)
                self._speed_to(self.vref, dt)
            else:
                # hold the final-fix height (and a safe height over terrain) until the glide slope arrives
                floor = sim.ground(t["north"], t["east"]) + 150.0
                self._altitude_to(max(min(self._fix_altitude(rw), h_gs), floor), dt)
                self._speed_to(self.vref * (1.05 if dist_aim < 12000 else 1.25), dt)
            if hagl_rw < 20 and along > -400:
                self._set_mode("FLARE")
            self.status = (f"APPROACH {rw.code} {rw.heading_deg:03.0f}  {dist_aim / 1852:.1f} nm  "
                           f"{'G/S' if self._targets.get('gs') else 'LOC' if established else 'intercept'} "
                           f"{t['altitude'] - h_gs:+.0f} m  LOC {right:+.0f} m")
            return
        if self.mode == "FLARE":
            hagl = max(agl, 0.0)
            self._gamma_to(-3.0 + 2.5 * (1 - min(hagl, 20) / 20), dt)
            self._heading_to(rw.heading_deg + float(np.clip(-0.05 * right, -8, 8)), 5.0)
            if hagl < 5:
                c.throttle = 0.0
                self._thr_i = 0.0
            else:
                self._speed_to(self.vref, dt)
            if t["on_ground"]:
                self._set_mode("ROLLOUT")
            self.status = f"FLARE  {hagl * 3.281:.0f} ft"
            return
        if self.mode == "ROLLOUT":
            touch_and_go = self.program == "circuits" and t["fuel_kg"] > self.reserve_fraction * sim.m.fuel_capacity_kg
            self._ground_steer(rw)
            c.elevator = 0.0
            if touch_and_go and sim.time - self._mode_time > 2.5:
                self._end_lap(sim.touchdown and {"sink_ms": sim.touchdown["sink_ms"]})
                sim.touchdown = None
                sim.airborne_since = None
                self.runway = rw
                self._set_mode("TAKEOFF")
                self.status = "TOUCH-AND-GO"
                return
            c.throttle, c.brakes = 0.0, True
            self._thr_i = 0.0
            if sim.landing:
                self._end_lap(sim.landing)
                L = sim.landing
                self.status = (f"LANDED {rw.code}: {L['feel']}, score {L['score']:.0f}/100 — "
                               f"{len(self.laps)} lap(s), fuel left {t['fuel_kg']:.0f} kg")
                self.program, self.mode = "off", "OFF"
            else:
                self.status = "ROLLOUT  braking"
