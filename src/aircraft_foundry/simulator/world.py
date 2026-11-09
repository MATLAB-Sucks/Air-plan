"""Simulator worlds: the procedural test range, or real-world areas built from open data.

Real worlds live in ``simulator/worlds/<name>/`` (built by ``tools/build_world.py`` on GitHub):
heights, a satellite-imagery texture, and runways / airfields / places from OpenStreetMap.
Both kinds offer the same interface as :class:`terrain.Terrain` plus ``runways``, ``home``,
``title``, ``texture_path``, ``attribution`` and ``places``.
"""
from __future__ import annotations

import json
import math
import re
from functools import cached_property
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

from .terrain import HOME, RUNWAYS, WATER_LEVEL, Runway, Terrain

WORLDS_DIR = Path(__file__).resolve().parent / "worlds"
PROCEDURAL = "test-range"


class ProceduralWorld(Terrain):
    """The fictional 120 km test range with four airports."""
    name = PROCEDURAL
    title = "Test range (fictional, four airports)"
    texture_path = None
    attribution: list = []
    places: list = []

    @property
    def runways(self):
        return RUNWAYS

    @property
    def home(self):
        return HOME


class RealWorld:
    def __init__(self, name: str):
        folder = WORLDS_DIR / name
        self.name = name
        self.meta = json.loads((folder / "world.json").read_text())
        self.title = self.meta.get("title", name.title())
        self.attribution = self.meta.get("attribution", [])
        self.places = self.meta.get("places", [])
        tex = folder / "texture.jpg"
        self.texture_path = tex if tex.exists() and self.meta.get("imagery", True) else None
        d = np.load(folder / "height.npz")
        self._north, self._east = d["north"].astype(float), d["east"].astype(float)
        self._h = d["height"].astype(float) / 10.0
        self.runways = tuple(self._make_runways(self.meta.get("runways", [])))
        self.home = self._pick_home()

    # ---- runways
    @staticmethod
    def _code(rw: dict) -> str:
        base = rw.get("icao") or "".join(w[0] for w in re.findall(r"[A-Za-z]+", rw.get("airfield", "AF")))[:4].upper()
        return f"{base} {(rw.get('ref') or '').split('/')[0] or round(rw['heading_deg'] / 10):0>2}".strip()

    @staticmethod
    def _clean(raw: list) -> list:
        """OpenStreetMap often maps a runway twice (centre line and outline) and has short unnamed
        fragments: merge duplicates (same airfield, same direction, overlapping) and drop fragments."""
        keep = []
        for rw in sorted(raw, key=lambda r: (not r.get("ref"), -r["length"])):
            if not rw.get("ref") and rw["length"] < 500:
                continue
            if rw.get("ref") and rw["length"] < 350:
                continue
            mid = (rw["north"] + 0.5 * rw["length"] * math.cos(math.radians(rw["heading_deg"])),
                   rw["east"] + 0.5 * rw["length"] * math.sin(math.radians(rw["heading_deg"])))
            dup = False
            for k in keep:
                kmid = (k["north"] + 0.5 * k["length"] * math.cos(math.radians(k["heading_deg"])),
                        k["east"] + 0.5 * k["length"] * math.sin(math.radians(k["heading_deg"])))
                same_axis = abs(((rw["heading_deg"] - k["heading_deg"]) + 90) % 180 - 90) < 12
                if same_axis and math.hypot(mid[0] - kmid[0], mid[1] - kmid[1]) < 0.5 * max(k["length"], 300):
                    dup = True
                    if not k.get("ref") and rw.get("ref"):
                        k["ref"] = rw["ref"]
                    # centre lines often carry a default width; the outline measures the real one
                    k["width"] = min(max(k["width"], rw["width"]), 80.0)
                    break
            if not dup:
                keep.append(dict(rw))
        return keep

    def _make_runways(self, raw: list) -> list[Runway]:
        out, seen = [], set()
        for rw in self._clean(raw):
            hdg = rw["heading_deg"]
            ref = rw.get("ref") or ""
            n0, e0 = rw["north"], rw["east"]
            m = re.match(r"(\d{1,2})", ref)
            if m:  # land in the direction of the first number (e.g. "09/27" -> heading about 090)
                want = int(m.group(1)) * 10
                if abs((hdg - want + 180) % 360 - 180) > 90:
                    rad = math.radians(hdg)
                    n0, e0 = n0 + rw["length"] * math.cos(rad), e0 + rw["length"] * math.sin(rad)
                    hdg = (hdg + 180) % 360
            code = self._code({**rw, "heading_deg": hdg})
            while code in seen:
                code += "'"
            seen.add(code)
            out.append(Runway(code, rw.get("airfield", "Airfield"), n0, e0, hdg, rw["length"], rw["width"],
                              rw["elevation"]))
        return out

    def _pick_home(self):
        if not self.runways:
            return HOME
        # the runway nearest the reference point (the world's namesake airfield), longest first
        return min(self.runways, key=lambda r: (round(math.hypot(*r.point(r.length / 2)) / 2000), -r.length))

    # ---- terrain interface
    @cached_property
    def grid(self):
        return self._north, self._east, self._h

    def height(self, north, east):
        north, east = np.asarray(north, float), np.asarray(east, float)
        i = (north - self._north[0]) / (self._north[1] - self._north[0])
        j = (east - self._east[0]) / (self._east[1] - self._east[0])
        return map_coordinates(self._h, [np.atleast_1d(i), np.atleast_1d(j)], order=1, mode="nearest").reshape(north.shape)

    def height_at(self, north: float, east: float) -> float:
        nn, ee, h = self._north, self._east, self._h
        i = (north - nn[0]) / (nn[1] - nn[0])
        j = (east - ee[0]) / (ee[1] - ee[0])
        i = min(max(i, 0.0), len(nn) - 1.001)
        j = min(max(j, 0.0), len(ee) - 1.001)
        i0, j0 = int(i), int(j)
        fi, fj = i - i0, j - j0
        return float((h[i0, j0] * (1 - fi) + h[i0 + 1, j0] * fi) * (1 - fj)
                     + (h[i0, j0 + 1] * (1 - fi) + h[i0 + 1, j0 + 1] * fi) * fj)

    def surface_height(self, north: float, east: float) -> float:
        h = self.height_at(north, east)
        for rw in self.runways:  # runways are flat at their surveyed elevation
            if rw.contains(north, east, 20.0):
                return rw.elevation
        return h

    def colours(self) -> np.ndarray:
        """Per-grid-point colours (minimap / fallback): sampled from the imagery, else by height."""
        n = self._h.shape
        if self.texture_path is not None:
            from PIL import Image
            img = np.asarray(Image.open(self.texture_path).convert("RGB").resize((n[1], n[0])), dtype=np.uint8)
            return np.ascontiguousarray(img[::-1])  # image row 0 is north; grid row 0 is south
        h = self._h
        c = np.zeros(n + (3,), dtype=np.uint8)
        c[:] = (112, 140, 74)
        c[h > np.percentile(h, 70)] = (78, 110, 60)
        return c

    def town(self, count: int = 0):
        return []


def available_worlds() -> list[tuple[str, str]]:
    """(name, title) for every world: real ones first."""
    out = []
    if WORLDS_DIR.exists():
        for f in sorted(WORLDS_DIR.iterdir()):
            if (f / "world.json").exists() and (f / "height.npz").exists():
                try:
                    out.append((f.name, json.loads((f / "world.json").read_text()).get("title", f.name)))
                except Exception:
                    pass
    return out + [(PROCEDURAL, ProceduralWorld.title)]


def load_world(name: str | None = None):
    names = [n for n, _ in available_worlds()]
    if name is None or name not in names:
        name = names[0]
    return ProceduralWorld() if name == PROCEDURAL else RealWorld(name)
