"""Triangle meshes and rigid placements.

Coordinate convention used throughout Aircraft Foundry (aircraft body axes,
as in Raymer):  +x points aft from the nose tip, +y to starboard, +z up.
All lengths are metres, all angles are stored in degrees.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Placement:
    """Rigid placement of a component frame in the aircraft frame.

    Rotation is applied as roll (about x), then pitch (about y), then yaw
    (about z), followed by the translation.
    """

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def matrix(self) -> np.ndarray:
        rx, ry, rz = np.radians(self.rotation_deg)
        cx, sx = np.cos(rx), np.sin(rx)
        cy, sy = np.cos(ry), np.sin(ry)
        cz, sz = np.cos(rz), np.sin(rz)
        mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        m = np.eye(4)
        m[:3, :3] = mz @ my @ mx
        m[:3, 3] = self.position
        return m

    def apply(self, points: np.ndarray) -> np.ndarray:
        m = self.matrix()
        return points @ m[:3, :3].T + m[:3, 3]

    def to_dict(self) -> dict:
        return {"position": list(self.position), "rotation_deg": list(self.rotation_deg)}

    @classmethod
    def from_dict(cls, d: dict) -> "Placement":
        return cls(tuple(d["position"]), tuple(d["rotation_deg"]))


@dataclass
class Mesh:
    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int64, counter-clockwise seen from outside
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.vertices = np.ascontiguousarray(self.vertices, dtype=np.float64)
        self.faces = np.ascontiguousarray(self.faces, dtype=np.int64)

    @property
    def n_triangles(self) -> int:
        return int(self.faces.shape[0])

    def transformed(self, placement: Placement) -> "Mesh":
        return Mesh(placement.apply(self.vertices), self.faces.copy())

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def signed_volume(self) -> float:
        v = self.vertices[self.faces]
        return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0)

    def face_normals(self) -> np.ndarray:
        v = self.vertices[self.faces]
        n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        return n / np.where(length > 0, length, 1.0)

    def is_closed_manifold(self) -> bool:
        """True if every directed edge is matched by exactly one opposite edge."""
        f = self.faces
        edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        keyed = edges[:, 0] * (len(self.vertices) + 1) + edges[:, 1]
        rev = edges[:, 1] * (len(self.vertices) + 1) + edges[:, 0]
        uniq, counts = np.unique(keyed, return_counts=True)
        if np.any(counts != 1):
            return False
        return bool(np.all(np.isin(rev, uniq)))

    @staticmethod
    def merge(meshes: list["Mesh"]) -> "Mesh":
        if not meshes:
            return Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))
        verts, faces, offset = [], [], 0
        for m in meshes:
            verts.append(m.vertices)
            faces.append(m.faces + offset)
            offset += len(m.vertices)
        return Mesh(np.vstack(verts), np.vstack(faces))
