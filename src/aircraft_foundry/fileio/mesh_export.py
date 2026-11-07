"""STL (binary) and OBJ export of triangulated components (aircraft frame, metres)."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ..core.components import Component
from ..geometry.mesh import Mesh
from .common import safe_name


def write_stl(path: str | Path, mesh: Mesh, name: str = "aircraft_foundry") -> Path:
    path = Path(path)
    tri = mesh.vertices[mesh.faces].astype(np.float32)
    normals = mesh.face_normals().astype(np.float32)
    rec = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = normals, tri
    with open(path, "wb") as fh:
        header = f"Aircraft Foundry STL: {name}".encode()[:80].ljust(80, b" ")
        fh.write(header)
        fh.write(struct.pack("<I", len(tri)))
        fh.write(rec.tobytes())
    return path


def read_stl_triangle_count(path: str | Path) -> int:
    with open(path, "rb") as fh:
        fh.seek(80)
        return struct.unpack("<I", fh.read(4))[0]


def export_stl(components: list[Component], path: str | Path) -> Path:
    """One STL containing all components merged (STL has no object structure)."""
    return write_stl(path, Mesh.merge([c.world_mesh() for c in components]), Path(path).stem)


def export_obj(components: list[Component], path: str | Path) -> Path:
    """One OBJ with a named object (``o``) per component, so structure survives."""
    path = Path(path)
    lines = ["# Aircraft Foundry OBJ export", "# units: metres; axes: x aft, y starboard, z up"]
    offset = 1
    for c in components:
        m = c.world_mesh()
        lines.append(f"o {safe_name(c.id)}")
        lines.append(f"# type={c.type.value} name={c.name}")
        lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in m.vertices)
        lines.extend(f"f {a + offset} {b + offset} {d + offset}" for a, b, d in m.faces)
        offset += len(m.vertices)
    path.write_text("\n".join(lines) + "\n")
    return path


def export_each(components: list[Component], directory: str | Path, fmt: str) -> list[Path]:
    """One file per component."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for c in components:
        target = directory / f"{safe_name(c.id)}.{fmt}"
        out.append(export_stl([c], target) if fmt == "stl" else export_obj([c], target))
    return out
