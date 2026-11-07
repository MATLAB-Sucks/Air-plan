import numpy as np
import pytest

from aircraft_foundry.geometry.airfoil import NacaAirfoil
from aircraft_foundry.geometry.fuselage import FuselageShape
from aircraft_foundry.geometry.loft import Loft, ellipse_loop, loft_to_mesh
from aircraft_foundry.geometry.mesh import Placement
from aircraft_foundry.geometry.planform import LiftingSurface


@pytest.mark.parametrize("t", [0.08, 0.12, 0.18])
def test_airfoil_leading_edge_index_and_thickness(t):
    af = NacaAirfoil(0.02, 0.4, t)
    loop = af.loop(40)
    assert len(loop) == 81
    assert loop[len(loop) // 2, 0] == pytest.approx(0.0, abs=1e-12)  # CAD relies on this
    xs = np.linspace(0, 1, 400)
    assert (2 * af.thickness_dist(xs)).max() == pytest.approx(t, rel=0.01)
    te_gap = loop[0, 1] - loop[-1, 1]
    assert 0 < te_gap < 0.005  # small finite trailing edge


def _circle_sections(n=4, r=1.0):
    return [np.column_stack([np.full(24, x), ellipse_loop(24, r, r)]) for x in np.linspace(0, 3, n)]


def test_solid_loft_is_closed_and_outward():
    m = loft_to_mesh(Loft(_circle_sections()))
    assert m.is_closed_manifold()
    assert m.signed_volume() == pytest.approx(np.pi * 3, rel=0.03)


def test_reversed_sections_still_outward():
    secs = [s[::-1] for s in _circle_sections()]
    assert loft_to_mesh(Loft(secs)).signed_volume() > 0


def test_hollow_loft_volume():
    outer = _circle_sections(2, 1.0)
    inner = _circle_sections(2, 0.8)
    m = loft_to_mesh(Loft(outer, inner))
    assert m.is_closed_manifold()
    assert m.signed_volume() == pytest.approx(np.pi * (1 - 0.64) * 3, rel=0.03)


def test_loft_rejects_mismatched_sections():
    with pytest.raises(ValueError):
        Loft([np.zeros((4, 3)), np.zeros((5, 3))])


def test_placement_matrix_rotation_and_translation():
    p = Placement((1, 2, 3), (0, 90, 0))
    out = p.apply(np.array([[1.0, 0, 0]]))[0]
    assert np.allclose(out, [1, 2, 2])  # +x rotated nose-up onto -z, then translated


def test_planform_reference_quantities():
    s = LiftingSurface(span=30, root_chord=5, taper_ratio=0.4, sweep_le_deg=25)
    assert s.area == pytest.approx(30 * (5 + 2) / 2)
    assert s.aspect_ratio == pytest.approx(30**2 / s.area)
    assert s.mac == pytest.approx(2 / 3 * 5 * (1 + 0.4 + 0.16) / 1.4)
    assert np.degrees(s.sweep_at(0.0)) == pytest.approx(25)


def test_wing_box_volume_positive_and_scales():
    s = LiftingSurface(30, 5, 0.4, 25, tc_root=0.14, tc_tip=0.10)
    big = LiftingSurface(30, 10, 0.4, 25, tc_root=0.14, tc_tip=0.10)
    v1, v2 = s.box_volume(0.15, 0.65), big.box_volume(0.15, 0.65)
    assert v1 > 0 and v2 / v1 == pytest.approx(4, rel=0.02)  # chord^2 scaling


def test_fuselage_cabin_and_shape():
    f = FuselageShape(40, 4)
    assert float(f.half_width(20)) == pytest.approx(2.0)
    assert float(f.half_width(0)) < 0.1
    assert f.cabin_length > 0.6 * f.length
