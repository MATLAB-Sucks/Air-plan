"""Build a real-world flight-simulator world from open data (run on GitHub Actions, which has internet).

    python tools/build_world.py lasham 51.1872 -1.0335 40

Writes ``src/aircraft_foundry/simulator/worlds/<name>/``:

* ``height.npz``   terrain heights (metres above sea level) on a north/east grid centred on the
                   reference point, from the Terrarium elevation tiles (Mapzen / AWS open data,
                   derived from SRTM, GMTED and others);
* ``texture.jpg``  ground imagery from the Sentinel-2 cloudless mosaic (EOX IT Services GmbH,
                   CC BY-NC-SA 4.0, contains modified Copernicus Sentinel data);
* ``world.json``   reference point, grid, runways (thresholds, headings, lengths, widths, surfaces),
                   airfields and place names from OpenStreetMap (ODbL), and the attributions.
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

R_EARTH = 6371000.0
UA = {"User-Agent": "aircraft-foundry-world-builder (github.com/MATLAB-Sucks/Air-plan)"}
DEM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
IMG_URLS = ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2021_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg",
            "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/GoogleMapsCompatible/{z}/{y}/{x}.jpg"]
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def get(url: str, data: bytes | None = None, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as exc:  # retry politely
            if i == tries - 1:
                raise
            print(f"  retry {url[:80]}... ({exc})")
            time.sleep(3 * (i + 1))


def to_tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def latlon(north: np.ndarray, east: np.ndarray, lat0: float, lon0: float):
    lat = lat0 + np.degrees(north / R_EARTH)
    lon = lon0 + np.degrees(east / (R_EARTH * math.cos(math.radians(lat0))))
    return lat, lon


def local(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (math.radians(lat - lat0) * R_EARTH,
            math.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0)))


def mosaic(url: str, z: int, lat0, lon0, half: float, decode) -> tuple[np.ndarray, int, int]:
    lat_n, lon_w = latlon(np.array(half), np.array(-half), lat0, lon0)
    lat_s, lon_e = latlon(np.array(-half), np.array(half), lat0, lon0)
    x0, y0 = (int(v) for v in to_tile(float(lat_n), float(lon_w), z))
    x1, y1 = (int(v) for v in to_tile(float(lat_s), float(lon_e), z))
    rows = []
    for ty in range(y0, y1 + 1):
        row = []
        for tx in range(x0, x1 + 1):
            row.append(decode(get(url.format(z=z, x=tx, y=ty))))
        rows.append(np.concatenate(row, axis=1))
    print(f"  {len(rows) * len(rows[0]) if rows else 0} tiles at zoom {z}")
    return np.concatenate(rows, axis=0), x0, y0


def sample(img: np.ndarray, z: int, x0: int, y0: int, lat, lon) -> np.ndarray:
    tx, ty = to_tile(0, 0, z)  # placeholder to keep signature simple
    n = 2 ** z
    px = ((lon + 180.0) / 360.0 * n - x0) * 256.0
    py = ((1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n - y0) * 256.0
    px = np.clip(px, 0, img.shape[1] - 1.001)
    py = np.clip(py, 0, img.shape[0] - 1.001)
    i0, j0 = py.astype(int), px.astype(int)
    fi, fj = py - i0, px - j0
    if img.ndim == 3:
        fi, fj = fi[..., None], fj[..., None]
    return ((img[i0, j0] * (1 - fi) + img[i0 + 1, j0] * fi) * (1 - fj)
            + (img[i0, j0 + 1] * (1 - fi) + img[i0 + 1, j0 + 1] * fi) * fj)


def terrarium(raw: bytes) -> np.ndarray:
    a = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.float64)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def rgb(raw: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.float64)


def osm(lat0, lon0, half) -> dict:
    lat_n, lon_w = latlon(np.array(half), np.array(-half), lat0, lon0)
    lat_s, lon_e = latlon(np.array(-half), np.array(half), lat0, lon0)
    bbox = f"{float(lat_s)},{float(lon_w)},{float(lat_n)},{float(lon_e)}"
    # one geometry mode per output statement: runway shapes need "geom", airfields / places "center"
    q = f"""[out:json][timeout:120];
way["aeroway"="runway"]({bbox});
out tags geom;
(way["aeroway"="aerodrome"]({bbox}); node["aeroway"="aerodrome"]({bbox}); relation["aeroway"="aerodrome"]({bbox});
 node["place"~"town|village|city"]({bbox}););
out tags center;"""
    for url in OVERPASS:
        try:
            return json.loads(get(url, urllib.parse.urlencode({"data": q}).encode()))
        except Exception as exc:
            print(f"  Overpass {url} failed: {exc}")
    raise RuntimeError("OpenStreetMap (Overpass) unavailable")


def runways_from_osm(data, lat0, lon0, dem_at) -> tuple[list, list, list]:
    aerodromes, runways, places = [], [], []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        if t.get("aeroway") == "aerodrome":
            c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
            if c:
                n, e = local(c["lat"], c["lon"], lat0, lon0)
                aerodromes.append({"name": t.get("name", "Airfield"), "icao": t.get("icao", ""), "north": n, "east": e})
        elif t.get("aeroway") == "runway" and el.get("geometry"):
            pts = [local(g["lat"], g["lon"], lat0, lon0) for g in el["geometry"]]
            P = np.array(pts)
            if len(P) > 2:  # area runway: use its long axis
                c = P.mean(0)
                u, s_, vt = np.linalg.svd(P - c)
                d = vt[0]
                proj = (P - c) @ d
                a_, b_ = c + proj.min() * d, c + proj.max() * d
                width_geom = float(np.ptp((P - c) @ vt[1]))
            else:
                a_, b_ = P[0], P[-1]
                width_geom = 0.0
            length = float(np.hypot(*(b_ - a_)))
            if length < 250:
                continue
            ref = t.get("ref", "")
            hdg = (math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0])) + 360) % 360
            try:
                width = float(t.get("width", "").split()[0])
            except (ValueError, IndexError):
                width = width_geom or (30.0 if t.get("surface", "") in ("asphalt", "concrete", "paved") else 40.0)
            mid = (a_ + b_) / 2
            runways.append({"ref": ref, "surface": t.get("surface", ""), "north": float(a_[0]), "east": float(a_[1]),
                            "heading_deg": hdg, "length": length, "width": max(min(width, 80.0), 15.0),
                            "elevation": float(dem_at(mid[0], mid[1]))})
        elif "place" in t and "lat" in el:
            n, e = local(el["lat"], el["lon"], lat0, lon0)
            places.append({"name": t.get("name", ""), "kind": t["place"], "north": n, "east": e})
    for rw in runways:  # name each runway after its nearest airfield
        near = min(aerodromes, key=lambda a: math.hypot(a["north"] - rw["north"], a["east"] - rw["east"]), default=None)
        rw["airfield"] = near["name"] if near else "Airfield"
        rw["icao"] = near["icao"] if near else ""
    return runways, aerodromes, places


DETAIL = {  # spacing of the height grid (m), terrain tile zoom, imagery zoom, texture size (px)
    "standard": (50.0, 12, 13, 2048),
    "high": (30.0, 13, 14, 4096),
}


def main(name: str, lat0: float, lon0: float, size_km: float, spacing: float = 50.0, detail: str = "standard",
         out_dir: str | None = None, title: str | None = None) -> None:
    spacing, dem_zoom, img_zoom, tex_size = DETAIL.get(detail, DETAIL["standard"])
    if size_km > 60 and detail == "high":
        img_zoom = 13  # keep the number of imagery tiles reasonable for big areas
    half = size_km * 500.0
    out = Path(out_dir) if out_dir else \
        Path(__file__).resolve().parents[1] / "src" / "aircraft_foundry" / "simulator" / "worlds" / name
    out.mkdir(parents=True, exist_ok=True)
    n = int(round(2 * half / spacing)) + 1
    north = np.linspace(-half, half, n)
    east = np.linspace(-half, half, n)
    N, E = np.meshgrid(north, east, indexing="ij")
    LAT, LON = latlon(N, E, lat0, lon0)
    print("terrain heights...")
    dem, dx0, dy0 = mosaic(DEM_URL, dem_zoom, lat0, lon0, half, terrarium)
    H = sample(dem, dem_zoom, dx0, dy0, LAT, LON)
    np.savez_compressed(out / "height.npz", north=north, east=east, height=np.round(H * 10).astype(np.int16))
    print(f"  heights {H.min():.0f} .. {H.max():.0f} m on a {n} x {n} grid")
    print("imagery...")
    tn = np.linspace(half, -half, tex_size)  # image row 0 = north edge
    te = np.linspace(-half, half, tex_size)
    TN, TE = np.meshgrid(tn, te, indexing="ij")
    TLAT, TLON = latlon(TN, TE, lat0, lon0)
    imagery = ""
    for url in IMG_URLS:
        try:
            img, ix0, iy0 = mosaic(url, img_zoom, lat0, lon0, half, rgb)
            tex = sample(img, img_zoom, ix0, iy0, TLAT, TLON)
            Image.fromarray(np.clip(tex, 0, 255).astype(np.uint8)).save(out / "texture.jpg", quality=86)
            imagery = url
            break
        except Exception as exc:
            print(f"  imagery from {url[:60]} failed: {exc}")

    def dem_at(nn, ee):
        la, lo = latlon(np.array(nn), np.array(ee), lat0, lon0)
        return float(sample(dem, dem_zoom, dx0, dy0, la, lo))
    print("OpenStreetMap...")
    data = osm(lat0, lon0, half)
    from collections import Counter
    kinds = Counter(f"{el['type']}:{el.get('tags', {}).get('aeroway') or el.get('tags', {}).get('place', '?')}"
                    f"{':geom' if el.get('geometry') else ''}" for el in data.get("elements", []))
    runways, aerodromes, places = runways_from_osm(data, lat0, lon0, dem_at)
    # readable through the GitHub API (annotations), unlike the raw log
    print(f"::notice title=OpenStreetMap::{dict(kinds)} -> {len(runways)} runways, {len(aerodromes)} airfields, "
          f"{len(places)} places; remark: {str(data.get('remark', ''))[:200]}")
    for rw in runways:
        print(f"  runway {rw['airfield']} {rw['ref']}: {rw['length']:.0f} m x {rw['width']:.0f} m, "
              f"heading {rw['heading_deg']:.0f}, {rw['surface']}, elevation {rw['elevation']:.0f} m")
    meta = {"name": name, "title": title or f"{name.title()} (real terrain)", "lat0": lat0, "lon0": lon0,
            "size_m": 2 * half, "detail": detail,
            "spacing_m": spacing, "runways": runways, "aerodromes": aerodromes, "places": places,
            "imagery": bool(imagery),
            "attribution": ["Terrain: Terrarium elevation tiles (Mapzen / AWS Open Data; SRTM, GMTED and others)",
                            "Imagery: Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH "
                            "(contains modified Copernicus Sentinel data), CC BY-NC-SA 4.0",
                            "Runways, airfields and places: (c) OpenStreetMap contributors, ODbL"]}
    (out / "world.json").write_text(json.dumps(meta, indent=1))
    print(f"wrote {out}")


AREAS = {  # the selectable flying areas: centre and a description
    "lasham": (51.1872, -1.0335, "Lasham, Hampshire - the UK's gliding capital"),
    "innsbruck": (47.2602, 11.3439, "Innsbruck - an airport deep in an Alpine valley"),
    "sf-bay": (37.6800, -122.3000, "San Francisco Bay - SFO, Oakland and the bay"),
    "queenstown": (-45.0211, 168.7392, "Queenstown, New Zealand - mountains and lakes"),
}

if __name__ == "__main__":
    a = sys.argv[1:] or ["lasham", "51.1872", "-1.0335", "40"]
    if a[0] == "area":  # build_world.py area <name> <size_km> <detail> <out_dir>
        lat, lon, title = AREAS[a[1]]
        main(a[1], lat, lon, float(a[2]), detail=a[3], out_dir=a[4], title=title)
    else:
        main(a[0], float(a[1]), float(a[2]), float(a[3]))
