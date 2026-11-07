import pytest

pytest.importorskip("vtkmodules.vtkRenderingCore")

from vtkmodules.vtkRenderingCore import vtkRenderer  # noqa: E402

from aircraft_foundry.core.design import AircraftDesign  # noqa: E402
from aircraft_foundry.rendering.scene import AircraftScene, mesh_to_polydata  # noqa: E402
from aircraft_foundry.structural_vis.styles import Layer, ViewMode, appearance  # noqa: E402


def test_polydata_conversion(params):
    m = AircraftDesign(params).component("vtail").local_mesh()
    poly = mesh_to_polydata(m)
    assert poly.GetNumberOfPolys() == m.n_triangles


def test_incremental_sync(params):
    d = AircraftDesign(params)
    scene = AircraftScene(vtkRenderer())
    first = scene.sync(d)
    assert first.added == len(d.components)
    actor_fus = scene.entries["fuselage"].actor
    d.set_parameter("wing.span", 38)
    s = scene.sync(d)
    assert s.reused > 0 and s.geometry_updated > 0
    assert scene.entries["fuselage"].actor is actor_fus  # untouched
    noop = scene.sync(d)
    assert noop.added == noop.geometry_updated == noop.moved == noop.removed == 0


def test_removed_components_leave_scene(params):
    d = AircraftDesign(params)
    scene = AircraftScene(vtkRenderer())
    scene.sync(d)
    d.set_parameter("wing.rib_pitch", 1.5)  # fewer ribs
    s = scene.sync(d)
    assert s.removed > 0 and set(scene.entries) == set(d.components)


def test_view_modes(params):
    d = AircraftDesign(params)
    rib, skin = d.component("wing.right.rib.01"), d.component("wing.right")
    all_layers = set(Layer)
    assert not appearance(rib, ViewMode.SOLID, all_layers).visible
    assert appearance(rib, ViewMode.STRUCTURE, all_layers).visible
    assert appearance(skin, ViewMode.TRANSPARENT, all_layers).opacity < 0.5
    assert not appearance(skin, ViewMode.SOLID, all_layers - {Layer.SKIN}).visible
    scene = AircraftScene(vtkRenderer())
    scene.sync(d)
    scene.highlight("wing.right.rib.01")
    assert scene.entries["wing.right.rib.01"].actor.GetVisibility()  # selection forces visibility
