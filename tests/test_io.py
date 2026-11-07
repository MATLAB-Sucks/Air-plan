import json

import pytest

from aircraft_foundry.core.design import AircraftDesign
from aircraft_foundry.core.generator import Candidate
from aircraft_foundry.core.project import Project
from aircraft_foundry.fileio.common import ExportSelection
from aircraft_foundry.fileio.exporter import ExportRequest, run_export
from aircraft_foundry.fileio.json_io import export_design_json, import_design_json
from aircraft_foundry.fileio.mesh_export import export_obj, export_stl, read_stl_triangle_count


@pytest.fixture
def design(params):
    return AircraftDesign(params, "Test aircraft")


def test_selection_excludes_structure_by_default(design):
    comps = ExportSelection().resolve(design)
    assert comps and not any(c.is_structure for c in comps)
    full = ExportSelection(include_structure=True).resolve(design)
    assert len(full) == len(design.components)


def test_selection_of_component_with_children(design):
    comps = ExportSelection(["wing.right"], include_structure=True).resolve(design)
    ids = {c.id for c in comps}
    assert "wing.right" in ids and "wing.right.spar.front" in ids and "wing.left" not in ids
    with pytest.raises(KeyError):
        ExportSelection(["nope"]).resolve(design)


def test_stl_triangle_count(design, tmp_path):
    comps = ExportSelection().resolve(design)
    path = export_stl(comps, tmp_path / "a.stl")
    assert read_stl_triangle_count(path) == sum(c.local_mesh().n_triangles for c in comps)
    assert path.stat().st_size == 84 + 50 * read_stl_triangle_count(path)


def test_obj_has_object_per_component(design, tmp_path):
    comps = ExportSelection().resolve(design)
    text = export_obj(comps, tmp_path / "a.obj").read_text()
    assert text.count("\no ") == len(comps)
    n_v = sum(len(c.local_mesh().vertices) for c in comps)
    assert sum(1 for line in text.splitlines() if line.startswith("v ")) == n_v


def test_design_json_roundtrip(design, mission, tmp_path):
    path = export_design_json(tmp_path / "d.json", design, mission)
    doc = json.loads(path.read_text())
    assert doc["components"][0]["id"] and doc["units"]["length"] == "m"
    d2, m2 = import_design_json(path)
    assert d2.params == design.params and m2 == mission


def test_project_roundtrip(params, mission, tmp_path):
    c = Candidate(params, name="X")
    c.evaluate(mission)
    proj = Project(mission, [c], c.id)
    saved = proj.save(tmp_path / "p")
    assert saved.suffix == ".afproj"
    loaded = Project.load(saved)
    assert loaded.candidates[0].params == params
    assert loaded.candidates[0].evaluation.score == pytest.approx(c.evaluation.score)
    assert loaded.active_id == c.id


def test_project_rejects_foreign_file(tmp_path):
    p = tmp_path / "x.afproj"
    p.write_text("{}")
    with pytest.raises(ValueError):
        Project.load(p)


def test_run_export_mesh_formats(design, mission, tmp_path):
    req = ExportRequest(tmp_path, ("stl", "obj", "json"), ExportSelection(["vtail"], True), per_component=True,
                        basename="fin")
    paths = run_export(req, design, mission)
    names = {p.name for p in paths}
    assert {"fin.stl", "fin.obj", "fin.json", "vtail.stl", "vtail.spar.front.obj"} <= names
