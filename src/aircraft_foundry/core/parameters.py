"""Aircraft design parameters.

Parameters are grouped (``wing``, ``fuselage`` ...).  Groups are the unit of
dependency tracking: a component generator declares which groups it reads,
and editing ``wing.span`` regenerates only generators that depend on
``wing``.  Each numeric field carries bounds/units in its metadata so the GUI
editor and the optimiser use one definition.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, asdict
from typing import Any


def p(default: float, lo: float, hi: float, unit: str = "", label: str = "", step: float | None = None,
      evolvable: bool = False, integer: bool = False):
    return field(default=default, metadata=dict(lo=lo, hi=hi, unit=unit, label=label,
                                                step=step or (hi - lo) / 100.0,
                                                evolvable=evolvable, integer=integer))


def choice(default: str, options: tuple[str, ...], label: str = ""):
    return field(default=default, metadata=dict(options=options, label=label))


LAYOUTS = ("conventional", "canard", "flying_wing", "blended_wing_body", "twin_boom", "supersonic_delta")


@dataclass
class ConfigurationParams:
    propulsion: str = choice("turbofan", ("turbofan", "turboprop", "none"), "Propulsion (none = glider)")
    engine_layout: str = choice("wing", ("wing", "rear_fuselage", "upper_aft", "pusher", "under_wing", "nose"),
                                "Engine layout")
    tail_type: str = choice("conventional", ("conventional", "t_tail", "v_tail", "twin_fin", "none"), "Tail type")
    wing_position: str = choice("low", ("low", "mid", "high"), "Wing position")
    layout: str = choice("conventional", LAYOUTS, "Aircraft layout")


@dataclass
class FuselageParams:
    length: float = p(37.0, 6.0, 110.0, "m", "Length")
    diameter: float = p(3.95, 0.8, 8.0, "m", "Max diameter")
    height_ratio: float = p(1.05, 0.8, 1.4, "", "Height / width")
    nose_fraction: float = p(0.12, 0.06, 0.25, "", "Nose length / L")
    tail_fraction: float = p(0.26, 0.15, 0.40, "", "Tail cone length / L")
    tail_upsweep: float = p(0.25, 0.0, 0.6, "D", "Tail upsweep (centre rise / D)")
    frame_pitch: float = p(0.9, 0.3, 2.0, "m", "Frame pitch")
    frame_depth: float = p(0.10, 0.03, 0.25, "m", "Frame depth")
    longerons: int = p(12, 4, 32, "", "Longerons", step=1, integer=True)
    area_ruling: float = p(0.0, 0.0, 0.9, "", "Area-rule waist (share of wing area removed)", step=0.05)
    nose_profile: str = choice("elliptic", ("elliptic", "ogive"), "Nose profile (ogive for supersonic)")


@dataclass
class WingParams:
    span: float = p(34.0, 4.0, 80.0, "m", "Span", evolvable=True)
    root_chord: float = p(6.2, 0.6, 90.0, "m", "Root chord (centre-body chord on a BWB)", evolvable=True)
    taper_ratio: float = p(0.25, 0.03, 1.0, "", "Taper ratio (tip / root)", evolvable=True)
    sweep_le_deg: float = p(27.0, 0.0, 75.0, "deg", "LE sweep", step=0.5, evolvable=True)
    vertical_shift: float = p(0.0, -0.5, 0.5, "", "Wing height fine-tune (fraction of fuselage half-height)", step=0.05)
    dihedral_deg: float = p(5.0, -5.0, 10.0, "deg", "Dihedral", step=0.5)
    twist_tip_deg: float = p(-3.0, -6.0, 2.0, "deg", "Tip twist", step=0.25)
    incidence_deg: float = p(1.5, -2.0, 5.0, "deg", "Root incidence", step=0.25)
    tc_root: float = p(0.15, 0.025, 0.20, "", "Root t/c", step=0.005, evolvable=True)
    tc_tip: float = p(0.11, 0.025, 0.16, "", "Tip t/c", step=0.005)
    camber: float = p(0.02, 0.0, 0.05, "", "Camber", step=0.005)
    camber_pos: float = p(0.4, 0.2, 0.6, "", "Camber position", step=0.05)
    x_position: float = p(0.36, 0.0, 0.75, "L", "Root LE station / L", step=0.005, evolvable=True)
    crank_eta: float = p(0.0, 0.0, 0.6, "eta", "Planform crank station (0 = none)", step=0.01)
    crank_chord_ratio: float = p(1.0, 0.15, 1.0, "c_root", "Chord at crank / root chord", step=0.01)
    inner_sweep_le_deg: float = p(27.0, 0.0, 80.0, "deg", "LE sweep inboard of crank", step=0.5)
    front_spar: float = p(0.15, 0.08, 0.30, "c", "Front spar", step=0.01)
    rear_spar: float = p(0.65, 0.50, 0.80, "c", "Rear spar", step=0.01)
    rib_pitch: float = p(0.75, 0.25, 2.0, "m", "Rib pitch", step=0.05)
    rib_thickness: float = p(0.04, 0.01, 0.15, "m", "Rib thickness", step=0.005)
    spar_thickness: float = p(0.06, 0.01, 0.2, "m", "Spar web thickness", step=0.005)


@dataclass
class TailParams:
    span: float = p(12.5, 0.5, 40.0, "m", "Span / height", evolvable=True)
    root_chord: float = p(3.6, 0.2, 12.0, "m", "Root chord", evolvable=True)
    taper_ratio: float = p(0.35, 0.2, 1.0, "", "Taper ratio")
    sweep_le_deg: float = p(32.0, 0.0, 60.0, "deg", "LE sweep", step=0.5)
    dihedral_deg: float = p(5.0, -5.0, 50.0, "deg", "Dihedral (V-tail: cant angle)", step=0.5)
    tc: float = p(0.10, 0.03, 0.15, "", "t/c", step=0.005)
    x_position: float = p(0.86, 0.0, 0.98, "L", "Root LE station / L", step=0.005)
    rib_pitch: float = p(0.6, 0.2, 2.0, "m", "Rib pitch", step=0.05)


@dataclass
class EngineParams:
    count: int = p(2, 1, 4, "", "Engine count (pairs except centreline pusher)", step=1, integer=True)
    thrust_kn: float = p(120.0, 2.0, 600.0, "kN", "Sea-level static thrust / engine", evolvable=True)
    span_fraction: float = p(0.34, 0.15, 0.6, "b/2", "Inboard engine station", step=0.01)
    spacing_fraction: float = p(0.25, 0.05, 0.4, "b/2", "Outboard engine offset", step=0.01)
    bypass_ratio: float = p(5.0, 0.0, 12.0, "", "Bypass ratio (turbofans)", step=0.1)
    x_shift: float = p(0.0, -15.0, 15.0, "m", "Engines moved forward (-) / aft (+)", step=0.1)
    glider_motor: str = choice("none", ("none", "sustainer", "self_launch"),
                               "Glider motor: none, sustainer (turbo, gets you home) or self-launching")
    glider_motor_kw: float = p(0.0, 0.0, 80.0, "kW", "Glider motor power", step=1.0)


@dataclass
class GearParams:
    nose_x_fraction: float = p(0.11, 0.04, 0.3, "L", "Nose gear station / L", step=0.005)
    main_chord_fraction: float = p(0.65, 0.2, 1.6, "c_root", "Main gear aft of root LE", step=0.01)
    track: float = p(7.0, 1.0, 16.0, "m", "Main gear track", step=0.1)
    wheel_diameter: float = p(1.1, 0.25, 1.8, "m", "Wheel diameter", step=0.01)
    ground_clearance: float = p(1.0, 0.25, 5.0, "m", "Fuselage ground clearance", step=0.01)
    wheels_per_leg: int = p(2, 1, 2, "", "Wheels per leg", step=1, integer=True)
    layout: str = choice("tricycle", ("tricycle", "glider_tail", "glider_nose"),
                         "Wheel layout (gliders: main + tail wheel, or nose + main + tail like a K-21)")


@dataclass
class ControlSurfaceParams:
    """Movable surfaces.  Spanwise stations are fractions of the semi-span (eta);
    chords are fractions of the local chord, measured forward from the trailing edge."""
    aileron_start: float = p(0.72, 0.1, 0.9, "eta", "Aileron inboard end", step=0.01)
    aileron_end: float = p(0.95, 0.6, 0.98, "eta", "Aileron outboard end", step=0.01)
    aileron_chord: float = p(0.25, 0.15, 0.50, "c", "Aileron chord", step=0.01)
    aileron_deflection_deg: float = p(0.0, -25.0, 25.0, "deg", "Aileron deflection (TE down +)", step=1)
    flap_end: float = p(0.68, 0.0, 0.85, "eta", "Flap outboard end (0 = no flaps)", step=0.01)
    flap_chord: float = p(0.30, 0.15, 0.40, "c", "Flap chord", step=0.01)
    flap_deflection_deg: float = p(0.0, 0.0, 45.0, "deg", "Flap deflection", step=1)
    elevator_chord: float = p(0.30, 0.15, 0.60, "c", "Elevator chord", step=0.01)
    elevator_deflection_deg: float = p(0.0, -25.0, 25.0, "deg", "Elevator deflection (TE down +)", step=1)
    rudder_chord: float = p(0.30, 0.15, 0.60, "c", "Rudder chord", step=0.01)
    rudder_deflection_deg: float = p(0.0, -30.0, 30.0, "deg", "Rudder deflection (TE to port +)", step=1)


MATERIALS = ("Al 7075-T6", "Al 2024-T3", "Al-Li 2099-T83", "Ti-6Al-4V", "CFRP quasi-isotropic")


@dataclass
class StructureParams:
    wing_material: str = choice("Al 7075-T6", MATERIALS, "Wing box material")
    fuselage_material: str = choice("Al 2024-T3", MATERIALS, "Fuselage shell material")
    limit_load_factor: float = p(2.5, 2.0, 12.0, "g", "Limit manoeuvre load factor", step=0.1)
    negative_load_factor: float = p(-1.0, -12.0, -0.5, "g", "Negative limit load factor (pushing / inverted)", step=0.1)
    safety_factor: float = p(1.5, 1.25, 2.0, "", "Ultimate safety factor", step=0.05)
    min_gauge_mm: float = p(1.0, 0.5, 3.0, "mm", "Minimum skin gauge", step=0.1)
    cabin_altitude_m: float = p(2438.0, 0.0, 3000.0, "m", "Cabin pressure altitude", step=50)
    weight_method: str = choice("statistical", ("statistical", "structural"), "Wing/fuselage weight method")
    insulation_kg_m2: float = p(1.5, 0.3, 20.0, "kg/m²", "Cabin acoustic insulation", step=0.1)
    # strength reserve per component: 1 = sized exactly to the design ultimate load; 2 = twice as strong
    wing_strength: float = p(1.0, 0.6, 3.0, "×", "Wing strength (reserve factor)", step=0.05)
    fuselage_strength: float = p(1.0, 0.6, 3.0, "×", "Fuselage strength (reserve factor)", step=0.05)
    tail_strength: float = p(1.0, 0.6, 3.0, "×", "Tail strength (reserve factor)", step=0.05)
    gear_strength: float = p(1.0, 0.6, 3.0, "×", "Landing-gear strength (reserve factor)", step=0.05)


@dataclass
class BoomParams:
    """Twin-boom layout: booms run from the wing to the tail."""
    lateral_station: float = p(0.30, 0.12, 0.6, "eta", "Boom spanwise station", step=0.01)
    diameter: float = p(0.6, 0.15, 2.0, "m", "Boom diameter", step=0.01)
    tail_arm: float = p(8.0, 1.5, 40.0, "m", "Wing root LE to tail root LE", step=0.1, evolvable=False)


def _canard_default() -> "TailParams":
    return TailParams(span=4.0, root_chord=1.2, taper_ratio=0.5, sweep_le_deg=10.0, dihedral_deg=0.0, tc=0.12,
                      x_position=0.10, rib_pitch=0.5)


GROUP_TYPES: dict[str, type] = {
    "configuration": ConfigurationParams,
    "fuselage": FuselageParams,
    "wing": WingParams,
    "htail": TailParams,
    "vtail": TailParams,
    "engines": EngineParams,
    "gear": GearParams,
    "controls": ControlSurfaceParams,
    "structure": StructureParams,
    "canard": TailParams,
    "booms": BoomParams,
}


def _vtail_default() -> TailParams:
    return TailParams(span=6.3, root_chord=5.2, taper_ratio=0.6, sweep_le_deg=38.0, dihedral_deg=0.0,
                      tc=0.11, x_position=0.80)


@dataclass
class AircraftParameters:
    configuration: ConfigurationParams = field(default_factory=ConfigurationParams)
    fuselage: FuselageParams = field(default_factory=FuselageParams)
    wing: WingParams = field(default_factory=WingParams)
    htail: TailParams = field(default_factory=TailParams)
    vtail: TailParams = field(default_factory=_vtail_default)
    engines: EngineParams = field(default_factory=EngineParams)
    gear: GearParams = field(default_factory=GearParams)
    controls: ControlSurfaceParams = field(default_factory=ControlSurfaceParams)
    structure: StructureParams = field(default_factory=StructureParams)
    canard: TailParams = field(default_factory=_canard_default)
    booms: BoomParams = field(default_factory=BoomParams)

    # ------------------------------------------------------------ access
    def get(self, path: str) -> Any:
        group, name = path.split(".")
        return getattr(getattr(self, group), name)

    def set(self, path: str, value: Any) -> None:
        group, name = path.split(".")
        obj = getattr(self, group)
        meta = field_meta(type(obj), name)
        if "options" in meta:
            if value not in meta["options"]:
                raise ValueError(f"{path}: {value!r} not in {meta['options']}")
        else:
            value = float(value)
            if not (meta["lo"] <= value <= meta["hi"]):
                raise ValueError(f"{path}={value} outside [{meta['lo']}, {meta['hi']}] {meta['unit']}")
            if meta.get("integer"):
                value = int(round(value))
        setattr(obj, name, value)

    def copy(self) -> "AircraftParameters":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_saved_dict(self) -> dict:
        """For files and the library: the fields plus the format version (see core.compat)."""
        from .compat import PARAMETERS_FORMAT
        return {**asdict(self), "_format_version": PARAMETERS_FORMAT}

    @classmethod
    def from_dict(cls, d: dict, report: list | None = None) -> "AircraftParameters":
        """Load any saved version: older files are migrated, newer fields ignored, values sanitised."""
        from .compat import upgrade_parameters
        return upgrade_parameters(d, report)

    def group_dict(self, group: str) -> dict:
        return asdict(getattr(self, group))


def field_meta(gtype: type, name: str) -> dict:
    for f in fields(gtype):
        if f.name == name:
            return dict(f.metadata)
    raise KeyError(f"{gtype.__name__} has no parameter {name!r}")


def parameter_specs() -> list[tuple[str, dict]]:
    """All parameters as (path, metadata) in display order."""
    out = []
    for group, gtype in GROUP_TYPES.items():
        for f in fields(gtype):
            out.append((f"{group}.{f.name}", dict(f.metadata)))
    return out


def evolvable_paths(params: AircraftParameters) -> list[str]:
    """Continuous design variables the optimiser may change.

    The configuration (topology) is held fixed during evolution, so every
    configuration shares the same variable set.
    """
    return [path for path, meta in parameter_specs() if meta.get("evolvable")]
