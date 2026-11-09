"""Real-world map builder: coordinate conversions, runway extraction, elevation decoding (offline)."""
import io
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_world as bw  # noqa: E402

LAT0, LON0 = 51.1872, -1.0335  # Lasham


def test_local_coordinates_round_trip():
    for n, e in ((0, 0), (12000, -8000), (-19000, 19500)):
        lat, lon = bw.latlon(np.array(float(n)), np.array(float(e)), LAT0, LON0)
        n2, e2 = bw.local(float(lat), float(lon), LAT0, LON0)
        assert n2 == pytest.approx(n, abs=0.5) and e2 == pytest.approx(e, abs=0.5)


def test_runway_from_an_osm_way():
    # a 1 800 m runway heading 090 (true) through the reference point, like Lasham's 09/27
    half = 900.0
    dlon = math.degrees(half / (bw.R_EARTH * math.cos(math.radians(LAT0))))
    way = {"type": "way", "tags": {"aeroway": "runway", "ref": "09/27", "surface": "asphalt", "width": "30"},
           "geometry": [{"lat": LAT0, "lon": LON0 - dlon}, {"lat": LAT0, "lon": LON0 + dlon}]}
    field = {"type": "node", "lat": LAT0, "lon": LON0, "tags": {"aeroway": "aerodrome", "name": "Lasham", "icao": "EGHL"}}
    rws, fields, _ = bw.runways_from_osm({"elements": [way, field]}, LAT0, LON0, lambda n, e: 188.0)
    rw = rws[0]
    assert rw["heading_deg"] == pytest.approx(90.0, abs=0.5) and rw["length"] == pytest.approx(1800, rel=0.01)
    assert rw["width"] == 30.0 and rw["airfield"] == "Lasham" and rw["icao"] == "EGHL" and rw["elevation"] == 188.0


def test_terrarium_decoding():
    from PIL import Image
    h = 188.4  # metres
    v = h + 32768
    r, g = int(v // 256), int(v % 256)
    b = int(round((v - int(v)) * 256))
    buf = io.BytesIO()
    Image.fromarray(np.full((2, 2, 3), (r, g, b), dtype=np.uint8)).save(buf, "PNG")
    assert bw.terrarium(buf.getvalue())[0, 0] == pytest.approx(h, abs=0.01)
