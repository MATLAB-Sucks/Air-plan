""""Will it fly?" - a plain-language airworthiness verdict for any parameter set.

Two separate questions:

1. **Can it fly at all?** (critical checks) - do the weights close, is it
   statically stable in pitch and yaw (or flyable with stability
   augmentation), can the elevator trim it for landing with flaps out, can
   the ailerons roll it, is there enough thrust to climb, is the structure
   strong enough.
2. **Does it meet the mission?** - every evaluator constraint that is not
   satisfied (range, runway, approach speed, noise, ...).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .evaluator import Evaluation, evaluate
from .mission import MissionRequirements
from .parameters import AircraftParameters


@dataclass
class Check:
    ok: bool
    name: str
    detail: str
    critical: bool = True
    warning: bool = False  # passes, but only with help (e.g. needs fly-by-wire)


@dataclass
class Verdict:
    flies: bool
    meets_mission: bool
    headline: str
    checks: list[Check] = field(default_factory=list)
    mission_issues: list[str] = field(default_factory=list)
    evaluation: Evaluation | None = None


STRUCTURE_WORDS = ("box", "gauge", "frame", "deflection", "shanley", "skin")


def flight_verdict(params: AircraftParameters, mission: MissionRequirements,
                   evaluation: Evaluation | None = None) -> Verdict:
    ev = evaluation or evaluate(params, mission)
    checks: list[Check] = []
    if not ev.converged:
        checks.append(Check(False, "Weights balance",
                            "The weight never closes: the aircraft is too heavy for its own wing, engines and fuel "
                            "(every kilogram added needs more structure and fuel, which adds more weight)."))
        return Verdict(False, False, "Won't fly: the design is too heavy to carry itself.", checks, [], ev)
    m = ev.metrics
    checks.append(Check(True, "Weights balance", f"Take-off weight {m['mtow_kg']:,.0f} kg closes."))

    from ..simulator.flightmodel import FlightSimulator, build_aero_model
    md = build_aero_model(params, mission, ev)
    sim = FlightSimulator(md)

    sm = m.get("static_margin", 0.0)
    if sm >= 0.02:
        checks.append(Check(True, "Stable in pitch", f"Static margin {sm:.0%}: returns to its trimmed attitude by itself."
                            + (" Very stable: needs a lot of elevator to rotate and flare." if sm > 0.4 else "")))
    elif sm > -0.06:
        checks.append(Check(True, "Stable in pitch", f"Static margin {sm:.0%}: neutral or slightly unstable - flyable "
                            "only with fly-by-wire stability augmentation.", warning=True))
    else:
        checks.append(Check(False, "Stable in pitch", f"Static margin {sm:.0%}: the nose would diverge up or down "
                            "faster than any pilot or computer could catch. Move the wing aft, move the tail "
                            "further back or make it bigger, or make a canard smaller."))

    if md.Cnb > 0.01:
        checks.append(Check(True, "Stable in yaw", f"Weathervane stability Cnβ = {md.Cnb:.3f}/rad."))
    elif md.Cndr > 0.003:
        checks.append(Check(True, "Stable in yaw", f"Cnβ = {md.Cnb:.3f}/rad: not naturally stable - flies with "
                            "yaw augmentation through the rudders / drag rudders (like the B-2).", warning=True))
    else:
        checks.append(Check(False, "Stable in yaw", "No directional stability and no rudder to fix it: it would "
                            "swap ends. Add or enlarge a fin."))

    vs_land = math.sqrt(2 * sim.mass * 9.80665 / (1.225 * md.S * (md.CLmax + md.flap_dCL)))
    vref = 1.23 * vs_land
    if md.glider:
        alpha, de, _ = sim.glide_trim(1.3 * vs_land, 300.0)
        ok, thr = True, 0.0
    else:
        alpha, de, thr, ok = sim.trim(vref, 300.0, 1.0, True)
    if ok and abs(de) <= 0.95:
        checks.append(Check(True, "Pitch control for landing", f"Trims at {vref * 1.944:.0f} kt with landing flaps "
                            f"using {abs(de):.0%} of elevator travel."))
    else:
        checks.append(Check(False, "Pitch control for landing", "The elevator can't hold the nose up at landing speed "
                            "with flaps down. Enlarge the tail or elevator, or move the wing/CG."))

    p_hat = md.Clda * 1.0 / max(-md.Clp, 1e-6)  # steady roll rate pb/2V at full aileron
    if p_hat >= 0.05:
        checks.append(Check(True, "Roll control", f"Full aileron gives pb/2V = {p_hat:.3f} (≥ 0.05 needed)."))
    else:
        checks.append(Check(False, "Roll control", f"pb/2V = {p_hat:.3f}: too sluggish in roll to be controllable "
                            "(≥ 0.05 needed). Make the ailerons longer or wider."))

    if md.glider:
        ld = ev.metrics.get("best_ld", 0.0)
        checks.append(Check(ld >= 15, "Glides", f"Best glide {ld:.0f}:1, minimum sink {ev.metrics.get('min_sink_ms', 0):.2f} "
                            "m/s - stays up in thermals." if ld >= 15 else
                            f"Best glide only {ld:.0f}:1: too much drag or too small a wing to soar."))
    else:
        alpha_c, _, thr_c, ok_c = sim.trim(1.4 * vs_land, 500.0, 0.4, False)
        climb_ok = ok_c and thr_c < 0.9
        checks.append(Check(climb_ok, "Enough thrust to climb",
                            f"Level flight just after take-off needs {thr_c:.0%} throttle." if ok_c else
                            "Can't even hold level flight just after take-off: more thrust or less drag needed."))

    cons = {c.name: c for c in ev.constraints}
    weak = [c.name for c in ev.violated if any(w in c.name.lower() for w in STRUCTURE_WORDS)]
    checks.append(Check(not weak, "Structure strong enough",
                        "Wing box and fuselage hold the design loads." if not weak else
                        "Would break or bend too much: " + ", ".join(weak) + "."))

    flies = all(c.ok for c in checks if c.critical)
    issues = [f"{c.name}: {c.value:.3g} (limit {c.lower if c.lower != -math.inf else ''}"
              f"{'–' if c.lower != -math.inf and c.upper != math.inf else ''}"
              f"{c.upper if c.upper != math.inf else ''})" for c in ev.violated if c.name not in weak]
    meets = flies and not ev.violated
    needs_fbw = any(c.warning for c in checks)
    if not flies:
        failed = [c.name.lower() for c in checks if not c.ok]
        headline = "Won't fly: " + "; ".join(failed) + "."
    elif meets:
        headline = "Flies, and meets every mission requirement." + (" (Needs stability augmentation.)" if needs_fbw else "")
    else:
        headline = f"Flies, but misses {len(issues)} mission requirement{'s' if len(issues) != 1 else ''}." + \
            (" (Needs stability augmentation.)" if needs_fbw else "")
    _ = cons
    return Verdict(flies, meets, headline, checks, issues, ev)
