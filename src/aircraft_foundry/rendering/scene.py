"""Incremental VTK scene for an :class:`AircraftDesign`.

Each component maps to one actor.  Geometry is uploaded in the component's
*local* frame and positioned with the actor's user matrix, so:

* unchanged components are never touched;
* a component that only moved gets a new matrix, no new geometry;
* only components whose geometry fingerprint changed are re-uploaded.

This keeps editing responsive on large models (hundreds of structural
members) instead of rebuilding the whole aircraft.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer

from ..core.components import Component
from ..core.design import AircraftDesign
from ..geometry.mesh import Mesh
from ..structural_vis.styles import HIGHLIGHT, Layer, ViewMode, appearance


def mesh_to_polydata(mesh: Mesh, smooth: bool = True):
    pts = vtkPoints()
    pts.SetData(numpy_to_vtk(np.ascontiguousarray(mesh.vertices), deep=True))
    n = len(mesh.faces)
    offsets = np.arange(0, 3 * n + 1, 3, dtype=np.int64)
    cells = vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(offsets, deep=True),
                  numpy_to_vtkIdTypeArray(np.ascontiguousarray(mesh.faces.ravel()), deep=True))
    poly = vtkPolyData()
    poly.SetPoints(pts)
    poly.SetPolys(cells)
    normals = vtkPolyDataNormals()
    normals.SetInputData(poly)
    normals.SetFeatureAngle(35.0)
    normals.SetSplitting(True)
    normals.ConsistencyOff()  # meshes are already consistently oriented
    normals.AutoOrientNormalsOff()
    normals.Update()
    out = vtkPolyData()
    out.ShallowCopy(normals.GetOutput())
    return out


def disable_vtk_key_shortcuts(style) -> None:
    """Turn off VTK's built-in keyboard shortcuts on an interactor style.

    VTK maps w/s to wireframe/surface for *every* actor, q/e to quit/exit,
    r to reset the camera, f to fly-to, p to pick and 3 to stereo.  The
    application provides its own controls, so these must not fire.  A style
    that has an observer for an event calls the observer instead of its
    default handler, so a no-op observer disables them.
    """
    for event in ("CharEvent", "KeyPressEvent", "KeyReleaseEvent"):
        style.AddObserver(event, lambda *_: None)


def matrix_to_vtk(m: np.ndarray) -> vtkMatrix4x4:
    vm = vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            vm.SetElement(i, j, float(m[i, j]))
    return vm


@dataclass
class _Entry:
    actor: vtkActor
    component: Component
    geom_fp: str
    placement_key: bytes


@dataclass
class SyncStats:
    added: int = 0
    geometry_updated: int = 0
    moved: int = 0
    removed: int = 0
    reused: int = 0


@dataclass
class AircraftScene:
    renderer: vtkRenderer
    mode: ViewMode = ViewMode.SOLID
    layers: set[Layer] = field(default_factory=lambda: {Layer.SKIN, Layer.STRUCTURE, Layer.ATTACHMENTS,
                                                         Layer.SYSTEMS})
    entries: dict[str, _Entry] = field(default_factory=dict)
    highlighted: str | None = None
    utilisation: dict[str, float] = field(default_factory=dict)
    _poly_cache: dict[str, vtkPolyData] = field(default_factory=dict)

    # ------------------------------------------------------------ sync
    def sync(self, design: AircraftDesign) -> SyncStats:
        stats = SyncStats()
        live = set(design.components)
        for cid in [k for k in self.entries if k not in live]:
            self.renderer.RemoveActor(self.entries.pop(cid).actor)
            stats.removed += 1
        for cid, comp in design.components.items():
            fp = comp.geometry_fingerprint
            pkey = np.round(comp.placement.matrix(), 9).tobytes()
            e = self.entries.get(cid)
            if e is None:
                actor = vtkActor()
                mapper = vtkPolyDataMapper()
                mapper.SetInputData(self._poly(comp))
                actor.SetMapper(mapper)
                actor.SetUserMatrix(matrix_to_vtk(comp.placement.matrix()))
                self.renderer.AddActor(actor)
                self.entries[cid] = _Entry(actor, comp, fp, pkey)
                stats.added += 1
            else:
                changed = False
                if e.geom_fp != fp:
                    e.actor.GetMapper().SetInputData(self._poly(comp))
                    e.geom_fp = fp
                    stats.geometry_updated += 1
                    changed = True
                if e.placement_key != pkey:
                    e.actor.SetUserMatrix(matrix_to_vtk(comp.placement.matrix()))
                    e.placement_key = pkey
                    stats.moved += 0 if changed else 1
                    changed = True
                e.component = comp
                if not changed:
                    stats.reused += 1
            self._apply(cid)
        self._trim_cache()
        return stats

    def _poly(self, comp: Component):
        fp = comp.geometry_fingerprint
        if fp not in self._poly_cache:
            self._poly_cache[fp] = mesh_to_polydata(comp.local_mesh())
        return self._poly_cache[fp]

    def _trim_cache(self) -> None:
        live = {e.geom_fp for e in self.entries.values()}
        if len(self._poly_cache) > 2 * max(len(live), 1):
            self._poly_cache = {k: v for k, v in self._poly_cache.items() if k in live}

    # ------------------------------------------------------------ appearance
    def _apply(self, cid: str) -> None:
        e = self.entries[cid]
        a = appearance(e.component, self.mode, self.layers, self.utilisation.get(cid))
        prop = e.actor.GetProperty()
        # a selected member is always shown, even when enclosed by skins in solid mode
        e.actor.SetVisibility(a.visible or cid == self.highlighted)
        colour = HIGHLIGHT if cid == self.highlighted else a.colour
        prop.SetColor(*colour)
        prop.SetOpacity(1.0 if cid == self.highlighted else a.opacity)
        prop.SetRepresentationToWireframe() if a.wireframe else prop.SetRepresentationToSurface()
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(30)
        prop.SetAmbient(0.12)
        prop.SetDiffuse(0.85)
        e.actor.SetPickable(a.visible and a.opacity > 0.15 or cid == self.highlighted)

    def set_mode(self, mode: ViewMode) -> None:
        self.mode = ViewMode(mode)
        for cid in self.entries:
            self._apply(cid)

    def set_utilisation(self, util: dict[str, float]) -> None:
        self.utilisation = dict(util)
        for cid in self.entries:
            self._apply(cid)

    def set_layer(self, layer: Layer, visible: bool) -> None:
        (self.layers.add if visible else self.layers.discard)(Layer(layer))
        for cid in self.entries:
            self._apply(cid)

    def highlight(self, cid: str | None) -> None:
        previous, self.highlighted = self.highlighted, cid
        for k in (previous, cid):
            if k in self.entries:
                self._apply(k)

    # ------------------------------------------------------------ queries
    def component_for_actor(self, actor) -> str | None:
        for cid, e in self.entries.items():
            if e.actor is actor:
                return cid
        return None

    def bounds(self) -> tuple[float, ...]:
        return self.renderer.ComputeVisiblePropBounds()

    def clear(self) -> None:
        for e in self.entries.values():
            self.renderer.RemoveActor(e.actor)
        self.entries.clear()
        self._poly_cache.clear()
        self.highlighted = None
