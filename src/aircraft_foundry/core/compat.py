"""Backwards (and forwards) compatibility for saved designs and missions.

Every saved parameter set and mission carries a ``_format_version``.  Loading goes through
:func:`upgrade_parameters` / :func:`upgrade_mission`, which

1. run the migrations from the file's format version up to the current one (renamed fields,
   changed units or meanings are translated here - one function per format step);
2. let the dataclasses fill in anything the file doesn't have with today's defaults
   (a design saved before a feature existed simply gets the default for it);
3. ignore anything the file has that this version doesn't know (a design saved by a *newer*
   version still opens);
4. sanitise values: numbers outside today's valid range are clamped, choices that no longer
   exist fall back to the default - each change is reported, never silently crashes.

Rule for future changes: never repurpose a field; add a new one with a default, and if a field
must be renamed or re-scaled, bump the format version and add a migration below.
"""
from __future__ import annotations

import copy
import math

PARAMETERS_FORMAT = 2
MISSION_FORMAT = 2


# ----------------------------------------------------------------------------- migrations
def _params_v1_to_v2(d: dict) -> dict:
    """<= 0.8 files (format 1): same field names.  Since 0.7 the structural weight follows the
    design's own g rating; older files keep the value they stored, so nothing to translate."""
    return d


def _mission_v1_to_v2(d: dict) -> dict:
    return d


PARAM_MIGRATIONS = {1: _params_v1_to_v2}
MISSION_MIGRATIONS = {1: _mission_v1_to_v2}


def _run(d: dict, current: int, migrations: dict) -> dict:
    d = copy.deepcopy(d)
    version = int(d.pop("_format_version", 1) or 1)
    while version < current:
        d = migrations[version](d)
        version += 1
    return d


def _sanitise(obj, specs, defaults, report: list | None, what: str) -> None:
    for name, meta in specs:
        parts = name.split(".")
        holder = obj
        dflt_holder = defaults
        for part in parts[:-1]:
            holder = getattr(holder, part)
            dflt_holder = getattr(dflt_holder, part)
        key = parts[-1]
        value = getattr(holder, key)
        default = getattr(dflt_holder, key)
        if "options" in meta:
            if value not in meta["options"]:
                setattr(holder, key, default)
                if report is not None:
                    report.append(f"{what} {name}: '{value}' is not available in this version — using '{default}'")
            continue
        lo, hi = meta.get("lo"), meta.get("hi")
        try:
            num = float(value)
        except (TypeError, ValueError):
            setattr(holder, key, default)
            if report is not None:
                report.append(f"{what} {name}: unreadable value {value!r} — using {default}")
            continue
        if not math.isfinite(num):
            num = float(default)
        clamped = num
        if lo is not None:
            clamped = max(clamped, lo)
        if hi is not None:
            clamped = min(clamped, hi)
        if isinstance(default, int) and not isinstance(default, bool):
            clamped = int(round(clamped))
        if clamped != value:
            setattr(holder, key, clamped)
            if report is not None and abs(float(clamped) - num) > 1e-9:
                report.append(f"{what} {name}: {value} is outside today's range — adjusted to {clamped}")


def upgrade_parameters(d: dict, report: list | None = None):
    from .parameters import GROUP_TYPES, AircraftParameters, parameter_specs
    d = _run(d, PARAMETERS_FORMAT, PARAM_MIGRATIONS)
    out = AircraftParameters()
    from dataclasses import asdict, fields
    for group, gtype in GROUP_TYPES.items():
        if not isinstance(d.get(group), dict):
            continue
        known = {f.name for f in fields(gtype)}
        vals = {k: v for k, v in d[group].items() if k in known}
        setattr(out, group, gtype(**{**asdict(getattr(out, group)), **vals}))
    specs = parameter_specs() if callable(parameter_specs) else parameter_specs
    _sanitise(out, specs, AircraftParameters(), report, "Design")
    return out


def upgrade_mission(d: dict, report: list | None = None):
    from dataclasses import fields
    from .mission import MissionRequirements, mission_specs
    d = _run(d, MISSION_FORMAT, MISSION_MIGRATIONS)
    known = {f.name for f in fields(MissionRequirements)}
    m = MissionRequirements(**{k: v for k, v in d.items() if k in known})
    _sanitise(m, mission_specs(), MissionRequirements(), report, "Mission")
    return m
