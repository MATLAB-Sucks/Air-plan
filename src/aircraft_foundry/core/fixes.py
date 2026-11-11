"""One-click fixes for common design problems: each failed check (or unmet requirement) maps to a
standard remedy a designer would try first.  Fixes edit a copy of the parameters and return it."""
from __future__ import annotations

import numpy as np

from .parameters import AircraftParameters


def _limits(p: AircraftParameters, key: str) -> tuple[float, float]:
    """A parameter's allowed range (from its field metadata)."""
    import dataclasses
    group, name = key.split(".", 1)
    for f in dataclasses.fields(getattr(p, group)):
        if f.name == name:
            return f.metadata.get("lo", -1e9), f.metadata.get("hi", 1e9)
    return -1e9, 1e9


def _clamp(p: AircraftParameters, key: str, value: float) -> None:
    lo, hi = _limits(p, key)
    p.set(key, float(np.clip(value, lo, hi)))


def _scale(p, keys, f):
    for k in keys:
        _clamp(p, k, p.get(k) * f)


def fix_stability(p, m):
    """Search the wing position for a healthy static margin (12 %; gliders 15 %)."""
    from .evaluator import evaluate
    target = 0.15 if p.configuration.propulsion == "none" else 0.12
    x0 = p.wing.x_position
    best = None
    for xp in np.linspace(max(0.15, x0 - 0.15), min(0.75, x0 + 0.15), 13):
        q = p.copy()
        _clamp(q, "wing.x_position", xp)
        ev = evaluate(q, m)
        if ev.converged and "static_margin" in ev.metrics:
            d = abs(ev.metrics["static_margin"] - target)
            if best is None or d < best[0]:
                best = (d, q)
    return best[1] if best else p


def fix_fin(p, m):
    _scale(p, ["vtail.span", "vtail.root_chord"], 1.1)  # ~ +20 % area
    return p


def fix_pitch_control(p, m):
    _clamp(p, "controls.elevator_chord", p.controls.elevator_chord + 0.08)
    _scale(p, ["htail.span", "htail.root_chord"], 1.08)
    return p


def fix_tailplane(p, m):
    _scale(p, ["htail.span", "htail.root_chord"], 1.1)
    return p


def fix_roll(p, m):
    _clamp(p, "controls.aileron_start", p.controls.aileron_start - 0.1)
    _clamp(p, "controls.aileron_chord", p.controls.aileron_chord + 0.05)
    return p


def fix_structure(p, m):
    _clamp(p, "structure.wing_strength", p.structure.wing_strength + 0.2)
    _clamp(p, "structure.fuselage_strength", p.structure.fuselage_strength + 0.1)
    return p


def fix_thrust(p, m):
    _scale(p, ["engines.thrust_kn"], 1.15)
    return p


def fix_wing_area(p, m):
    _scale(p, ["wing.root_chord"], 1.12)
    return p


def fix_span_limit(p, m):
    _clamp(p, "wing.span", 0.995 * m.max_span_m)
    return p


def fix_glide(p, m):
    _clamp(p, "wing.span", min(p.wing.span + 1.0, 0.995 * m.max_span_m))
    return p


def fix_g(p, m):
    _clamp(p, "structure.limit_load_factor", max(p.structure.limit_load_factor, getattr(m, "g_limit", 2.5)))
    return p


FIXES = {  # check / requirement name -> (button text, fix)
    "Stable in pitch": ("Move the wing for a healthy static margin", fix_stability),
    "Static margin": ("Move the wing for a healthy static margin", fix_stability),
    "Stable in yaw": ("Enlarge the fin", fix_fin),
    "Vertical tail volume": ("Enlarge the fin", fix_fin),
    "Pitch control for landing": ("Bigger elevator and tailplane", fix_pitch_control),
    "Horizontal tail volume": ("Enlarge the tailplane", fix_tailplane),
    "Roll control": ("Longer, wider ailerons", fix_roll),
    "Structure strong enough": ("Strengthen the structure", fix_structure),
    "Enough thrust to climb": ("15 % more thrust", fix_thrust),
    "Take-off field length": ("15 % more thrust", fix_thrust),
    "Approach speed": ("12 % more wing area", fix_wing_area),
    "Stall speed": ("12 % more wing area", fix_wing_area),
    "Wingspan": ("Trim the span to the limit", fix_span_limit),
    "Best glide ratio": ("More span (within the class limit)", fix_glide),
    "G rating": ("Raise the g rating to the requirement", fix_g),
}


def fixes_for(verdict) -> list[tuple[str, str, object]]:
    """[(problem, button text, fix)] for the failed checks and unmet requirements, without duplicates."""
    out, seen = [], set()
    names = [c.name for c in getattr(verdict, "checks", []) if not c.ok]
    ev = getattr(verdict, "evaluation", None)
    if ev is not None:
        names += [c.name for c in ev.constraints if c.enforced and not c.satisfied]
    for n in names:
        if n in FIXES and FIXES[n][1] not in seen:
            seen.add(FIXES[n][1])
            out.append((n, FIXES[n][0], FIXES[n][1]))
    return out


def apply_fix(fix, params: AircraftParameters, mission) -> AircraftParameters:
    return fix(params.copy(), mission)
