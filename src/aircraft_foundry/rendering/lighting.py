"""Studio lighting and camera presets."""
from __future__ import annotations

from vtkmodules.vtkRenderingCore import vtkLight, vtkRenderer

VIEWS = {
    "iso": ((-1.0, -1.2, 0.8), (0, 0, 1)),
    "front": ((-1.0, 0.0, 0.0), (0, 0, 1)),
    "side": ((0.0, -1.0, 0.0), (0, 0, 1)),
    "top": ((0.0, 0.0, 1.0), (-1, 0, 0)),
    "rear": ((1.0, 0.0, 0.15), (0, 0, 1)),
}


def setup_renderer(renderer: vtkRenderer) -> None:
    renderer.GradientBackgroundOn()
    renderer.SetBackground(0.16, 0.18, 0.21)
    renderer.SetBackground2(0.36, 0.40, 0.46)
    renderer.SetUseDepthPeeling(True)  # correct transparency ordering
    renderer.SetMaximumNumberOfPeels(8)
    renderer.SetOcclusionRatio(0.05)
    renderer.RemoveAllLights()
    for pos, intensity in (((-1, -1, 1.5), 0.85), ((1, 1, 0.6), 0.35), ((0, 0, -1), 0.15)):
        light = vtkLight()
        light.SetLightTypeToCameraLight()
        light.SetPosition(*pos)
        light.SetFocalPoint(0, 0, 0)
        light.SetIntensity(intensity)
        renderer.AddLight(light)


def set_view(renderer: vtkRenderer, name: str) -> None:
    direction, up = VIEWS[name]
    b = renderer.ComputeVisiblePropBounds()
    if b[0] > b[1]:
        return
    centre = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2]
    cam = renderer.GetActiveCamera()
    cam.SetFocalPoint(*centre)
    cam.SetPosition(*[c + d * 100 for c, d in zip(centre, direction)])
    cam.SetViewUp(*up)
    renderer.ResetCamera()
    renderer.ResetCameraClippingRange()
