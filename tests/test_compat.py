"""Backwards compatibility: designs saved by older (and newer) versions still open and analyse."""
import json
import sqlite3
from pathlib import Path

import pytest

from aircraft_foundry.core.compat import PARAMETERS_FORMAT
from aircraft_foundry.core.evaluator import evaluate
from aircraft_foundry.core.mission import MissionRequirements
from aircraft_foundry.core.parameters import AircraftParameters
from aircraft_foundry.fileio.json_io import import_design_json

FIX = Path(__file__).parent / "fixtures"


def test_design_saved_by_version_0_3_still_works():
    design, mission = import_design_json(FIX / "design_v0.3.json")
    p = design.params
    assert p.structure.wing_strength == 1.0 and p.structure.gear_strength == 1.0  # added later: defaults
    assert mission.g_limit == 2.5 and mission.category == "transport" and mission.ignore == ""
    ev = evaluate(p, mission)
    assert ev.converged and ev.feasible
    assert 70000 < ev.metrics["mtow_kg"] < 100000


def test_design_from_a_newer_version_opens_with_a_report():
    raw = json.loads((FIX / "design_future.json").read_text())
    report = []
    p = AircraftParameters.from_dict(raw["design"]["parameters"], report)
    assert p.controls.aileron_start == pytest.approx(0.1)  # clamped into today's range
    assert p.configuration.engine_layout == "wing"  # unknown option -> default
    assert any("aileron_start" in r for r in report) and any("engine_layout" in r for r in report)
    m = MissionRequirements.from_dict(raw["mission"])
    assert evaluate(p, m).converged
    design, mission = import_design_json(FIX / "design_future.json")  # the whole file opens too
    assert design.params.configuration.engine_layout == "wing"


def test_current_saves_carry_a_format_version_and_round_trip():
    p = AircraftParameters()
    d = p.to_saved_dict()
    assert d["_format_version"] == PARAMETERS_FORMAT
    assert AircraftParameters.from_dict(d) == p
    assert "_format_version" not in p.to_dict()  # plain dict for copies: AircraftParameters-style **kwargs
    m = MissionRequirements(g_limit=4.0, ignore="boom")
    assert MissionRequirements.from_dict(m.to_saved_dict()) == m
    assert MissionRequirements(**m.to_dict()) == m


def test_library_rows_written_by_older_versions_load(tmp_path):
    from aircraft_foundry.library.store import DesignLibrary
    lib = DesignLibrary(tmp_path / "lib.sqlite", "test")
    old = json.loads((FIX / "design_v0.3.json").read_text())
    new_id = lib.save(AircraftParameters(), MissionRequirements(), "placeholder")
    con = sqlite3.connect(tmp_path / "lib.sqlite")
    table = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
             if "parameters_json" in " ".join(c[1] for c in con.execute(f"PRAGMA table_info({r[0]})"))][0]
    con.execute(f"UPDATE {table} SET parameters_json=?, mission_json=?",
                (json.dumps(old["design"]["parameters"]), json.dumps(old["mission"])))
    con.commit()
    con.close()
    params, mission, _ = lib.load(new_id)
    assert params.structure.wing_strength == 1.0 and evaluate(params, mission).converged


def test_old_project_files_open(tmp_path):
    from aircraft_foundry.core.project import Project
    old = json.loads((FIX / "design_v0.3.json").read_text())
    proj = {"format": "aircraft-foundry-project", "version": 1, "mission": old["mission"],
            "candidates": [{"name": "old", "origin": "0.3", "parameters": old["design"]["parameters"]}]}
    path = tmp_path / "old.afproj"
    path.write_text(json.dumps(proj))
    pr = Project.load(path)
    assert pr.candidates and pr.candidates[0].params.structure.tail_strength == 1.0
