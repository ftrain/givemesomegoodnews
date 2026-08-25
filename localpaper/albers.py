"""Albers equal-area conic projection (the standard lower-48 USGS setup)
and an SVG renderer for the coverage map. No JavaScript, no tiles — the map
is a static inline SVG so the site stays plain HTML.
"""

import json
import math

# USGS/d3 conventional parameters for the conterminous United States.
LAMBDA0 = math.radians(-96.0)   # central meridian
PHI0 = math.radians(23.0)       # latitude of origin
PHI1 = math.radians(29.5)       # standard parallels
PHI2 = math.radians(45.5)

_N = (math.sin(PHI1) + math.sin(PHI2)) / 2
_C = math.cos(PHI1) ** 2 + 2 * _N * math.sin(PHI1)
_RHO0 = math.sqrt(_C - 2 * _N * math.sin(PHI0)) / _N

EXCLUDE_STATES = {"Alaska", "Hawaii", "Puerto Rico"}


def project(lon, lat):
    lam = math.radians(lon)
    phi = math.radians(lat)
    rho = math.sqrt(_C - 2 * _N * math.sin(phi)) / _N
    theta = _N * (lam - LAMBDA0)
    x = rho * math.sin(theta)
    y = _RHO0 - rho * math.cos(theta)
    return x, -y  # flip y for SVG's downward axis


def _iter_rings(geom):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


class MapProjection:
    """Fits projected lower-48 state outlines into an SVG viewport."""

    def __init__(self, geojson_path, width=940, pad=8):
        with open(geojson_path) as f:
            data = json.load(f)
        self.features = [
            f for f in data["features"]
            if f["properties"].get("name") not in EXCLUDE_STATES
        ]
        xs, ys = [], []
        for feat in self.features:
            for ring in _iter_rings(feat["geometry"]):
                for lon, lat in ring:
                    x, y = project(lon, lat)
                    xs.append(x)
                    ys.append(y)
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        self.scale = (width - 2 * pad) / (maxx - minx)
        self.tx = pad - minx * self.scale
        self.ty = pad - miny * self.scale
        self.width = width
        self.height = math.ceil((maxy - miny) * self.scale + 2 * pad)

    def to_svg_coords(self, lon, lat):
        x, y = project(lon, lat)
        return x * self.scale + self.tx, y * self.scale + self.ty

    def state_paths(self):
        for feat in self.features:
            parts = []
            for ring in _iter_rings(feat["geometry"]):
                pts = []
                for lon, lat in ring:
                    x, y = self.to_svg_coords(lon, lat)
                    pts.append(f"{x:.1f},{y:.1f}")
                parts.append("M" + "L".join(pts) + "Z")
            yield feat["properties"].get("name", ""), "".join(parts)
