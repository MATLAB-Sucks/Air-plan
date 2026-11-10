"""Mission requirements: the input to the design engine."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .parameters import choice, p


@dataclass
class MissionRequirements:
    name: str = "Short-medium haul transport"
    payload_kg: float = p(16000.0, 200.0, 120000.0, "kg", "Design payload")
    range_km: float = p(4500.0, 200.0, 15000.0, "km", "Design range")
    cruise_mach: float = p(0.78, 0.30, 2.4, "", "Cruise Mach (overwater)")
    cruise_altitude_m: float = p(11000.0, 3000.0, 20000.0, "m", "Cruise altitude")
    takeoff_field_m: float = p(2200.0, 600.0, 4000.0, "m", "Max takeoff field length")
    max_approach_speed_ms: float = p(72.0, 40.0, 95.0, "m/s", "Max approach speed")
    max_span_m: float = p(36.0, 8.0, 80.0, "m", "Max wingspan (gate limit)")
    propulsion: str = choice("auto", ("auto", "turbofan", "turboprop"), "Propulsion")
    engines: str = choice("auto", ("auto", "1", "2", "4"), "Engine count")
    explore: str = choice("all suitable layouts", ("all suitable layouts", "conventional only",
                                                    "unconventional only"), "Layouts to explore")
    objective: str = choice("min_mtow", ("min_mtow", "min_fuel"), "Objective")
    overland_fraction: float = p(0.0, 0.0, 1.0, "", "Share of the route over land")
    max_boom_pldb: float = p(75.0, 60.0, 110.0, "PLdB", "Max sonic boom on the ground (overland)")
    max_flyover_db: float = p(90.0, 70.0, 125.0, "dB", "Max airport noise (take-off flyover)")
    max_cabin_dba: float = p(82.0, 65.0, 95.0, "dB(A)", "Max cabin noise for passengers")
    g_limit: float = p(2.5, 2.0, 12.0, "g", "Required g rating (limit load factor)")
    passenger_mass_kg: float = p(100.0, 70.0, 130.0, "kg", "Mass per passenger incl. bags")
    seat_pitch_m: float = p(0.79, 0.7, 1.0, "m", "Seat pitch")
    # "aerobatic" = stunt plane: +-10 g, roll-rate and power requirements, symmetric wing, light-aircraft rules
    category: str = choice("transport", ("transport", "aerobatic", "glider"), "Aircraft category")
    ignore: str = ""  # comma-separated REQUIREMENTS keys the design should not be sized around
    notes: str = field(default="")

    # ------------------------------------------------------------ derived requirements
    @property
    def passengers(self) -> int:
        return max(1, int(round(self.payload_kg / self.passenger_mass_kg)))

    @property
    def seats_abreast(self) -> int:
        n = self.passengers
        if getattr(self, "category", "transport") in ("aerobatic", "glider"):
            return 1  # tandem cockpit (Extra 300 / Pitts style, sailplanes)
        if n <= 2:
            return n
        abreast = int(min(9, max(2, round(0.45 * math.sqrt(n)))))
        # supersonic: wave drag grows with the square of cross-section area, so cabins stay slim
        # (Concorde and Overture are 4-abreast) and get longer instead
        return min(abreast, 4) if self.cruise_mach > 1.0 else abreast

    @property
    def small_aircraft(self) -> bool:
        """Up to 6 seats: the pilot sits in one of the seats (no separate crew)."""
        return self.passengers <= 6

    @property
    def cabin_length_m(self) -> float:
        rows = math.ceil(self.passengers / self.seats_abreast)
        extra = 4.0 if self.passengers > 19 else 1.6  # galleys/lavatories/doors vs. cockpit & baggage
        return rows * self.seat_pitch_m + extra

    @property
    def cabin_floor_area_m2(self) -> float:
        """Floor area needed when the cabin is not a tube (blended wing body)."""
        return 0.62 * self.passengers + (12.0 if self.passengers > 19 else 2.0)

    @property
    def fuselage_diameter_m(self) -> float:
        if self.small_aircraft:  # no aisle: cockpit-style cabin
            return max(1.05, 0.62 * self.seats_abreast + 0.35)
        aisles = 2 if self.seats_abreast > 6 else 1
        return max(1.3, 0.52 * self.seats_abreast + 0.5 * aisles + 0.45)

    @property
    def crew_kg(self) -> float:
        if self.small_aircraft:
            return 0.0  # the pilot is one of the seated occupants (counted in the payload)
        return 2 * 95.0 + 80.0 * math.ceil(self.passengers / 50)

    # ------------------------------------------------------------ io
    def set(self, name: str, value) -> None:
        meta = {f.name: f.metadata for f in fields(self)}[name]
        if "options" in meta and value not in meta["options"]:
            raise ValueError(f"{name}: {value!r} not one of {meta['options']}")
        if "lo" in meta:
            value = float(value)
            if not meta["lo"] <= value <= meta["hi"]:
                raise ValueError(f"{name}={value} outside [{meta['lo']}, {meta['hi']}]")
        setattr(self, name, value)

    def validate(self) -> list[str]:
        problems = []
        for f in fields(self):
            m = f.metadata
            v = getattr(self, f.name)
            if "lo" in m and not (m["lo"] <= v <= m["hi"]):
                problems.append(f"{m['label']} = {v} outside [{m['lo']}, {m['hi']}] {m['unit']}")
            if "options" in m and v not in m["options"]:
                problems.append(f"{m['label']}: {v!r} not one of {m['options']}")
        if self.propulsion == "turboprop" and self.cruise_mach > 0.7:
            problems.append("Turboprop propulsion is limited to cruise Mach 0.70")
        if 0.9 < self.cruise_mach < 1.15:
            problems.append("Cruise Mach between 0.90 and 1.15 sits in the transonic drag rise; "
                            "choose <= 0.90 or >= 1.15")
        return problems

    @property
    def supersonic(self) -> bool:
        return self.cruise_mach > 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_saved_dict(self) -> dict:
        """For files and the library: the fields plus the format version (see core.compat)."""
        from .compat import MISSION_FORMAT
        return {**asdict(self), "_format_version": MISSION_FORMAT}

    @classmethod
    def from_dict(cls, d: dict, report: list | None = None) -> "MissionRequirements":
        """Load any saved version: older files are migrated, newer fields ignored, values sanitised."""
        from .compat import upgrade_mission
        return upgrade_mission(d, report)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_saved_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "MissionRequirements":
        return cls.from_dict(json.loads(Path(path).read_text()))


# Requirements the user can switch off.  Airworthiness checks (stability, tail sizing, trim,
# structure, gear geometry, clearances) are always enforced so every design stays flyable.
REQUIREMENTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("span", "Wingspan / airport gate limit", ("Wingspan",)),
    ("field", "Take-off field length", ("Take-off field length",)),
    ("approach", "Approach speed", ("Approach speed",)),
    ("oei", "Engine-out climb", ("OEI climb gradient",)),
    ("cruise", "Cruise speed (thrust and drag-divergence)", ("Cruise thrust margin", "Drag-divergence margin",
                                                            "Transonic acceleration margin (Mach 1.1)")),
    ("fuel", "Fuel fits in the wing", ("Wing fuel capacity ratio",)),
    ("cabin", "Cabin length for the passengers", ("Cabin length",)),
    ("airport_noise", "Airport noise", ("Airport noise (take-off flyover)",)),
    ("cabin_noise", "Cabin noise", ("Cabin noise",)),
    ("boom", "Sonic boom over land", ("Sonic boom overland",)),
    ("g_rating", "G rating", ("G rating",)),
    ("glider", "Glider performance (glide ratio, sink rate, stall speed)", (
        "Best glide ratio", "Minimum sink rate", "Stall speed")),
    ("aerobatic", "Aerobatic performance (roll rate, power, inverted)", (
        "Roll rate at manoeuvring speed", "Static thrust / weight", "Symmetric aerofoil (inverted flight)")),
]
REQUIREMENT_OF = {name: key for key, _, names in REQUIREMENTS for name in names}


def ignored_requirements(m) -> set[str]:
    return {k.strip() for k in (getattr(m, "ignore", "") or "").split(",") if k.strip()}


def mission_specs() -> list[tuple[str, dict]]:
    return [(f.name, dict(f.metadata)) for f in fields(MissionRequirements) if f.metadata]
