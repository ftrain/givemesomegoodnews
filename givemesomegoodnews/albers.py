"""Albers equal-area conic projections and an SVG renderer for the coverage
map. No JavaScript, no tiles — the map is a static inline SVG so the site
stays plain HTML.

The map is a composite, the way the standard albersUsa layout is: the lower
48 fill the frame, and Alaska, Hawaii and Puerto Rico are drawn as insets in
the empty ocean at the bottom, each with its own conic fitted to its own
latitudes. Alaska projected on the lower-48 cone would be an unreadable
smear across the top of the frame; giving it its own cone is what makes it
the shape people recognise.

Territories with no outline in data/us_states.geojson — Guam, the U.S.
Virgin Islands, American Samoa, the Northern Marianas — get a labelled
marker each, so a newsroom in one of them still has somewhere to sit.
"""

import json
import math


def _conic(lambda0_deg, phi0_deg, phi1_deg, phi2_deg):
    """Build an Albers equal-area conic for one region's latitudes."""
    lambda0 = math.radians(lambda0_deg)
    phi0, phi1, phi2 = map(math.radians, (phi0_deg, phi1_deg, phi2_deg))
    n = (math.sin(phi1) + math.sin(phi2)) / 2
    c = math.cos(phi1) ** 2 + 2 * n * math.sin(phi1)
    rho0 = math.sqrt(c - 2 * n * math.sin(phi0)) / n

    def project(lon, lat):
        rho = math.sqrt(c - 2 * n * math.sin(math.radians(lat))) / n
        theta = n * (math.radians(lon) - lambda0)
        return rho * math.sin(theta), -(rho0 - rho * math.cos(theta))

    return project


# Conventional USGS/d3 parameters per region.
LOWER48 = _conic(-96.0, 23.0, 29.5, 45.5)
ALASKA = _conic(-152.0, 55.0, 55.0, 65.0)
HAWAII = _conic(-157.0, 13.0, 8.0, 18.0)
PUERTO_RICO = _conic(-66.0, 13.0, 8.0, 18.0)

INSET_STATES = {"Alaska": "AK", "Hawaii": "HI", "Puerto Rico": "PR"}

# Inset boxes, as fractions of the main map's width and height. Alaska and
# Hawaii sit in the Pacific, Puerto Rico off Florida.
INSET_BOXES = {
    "AK": (0.005, 0.60, 0.20, 0.38),
    "HI": (0.215, 0.80, 0.095, 0.17),
    "PR": (0.870, 0.855, 0.085, 0.10),
}

# Territories with no geometry here: a labelled marker at a fixed spot.
TERRITORY_MARKERS = {
    "GU": ("Guam", 0.345, 0.905),
    "MP": ("N. Marianas", 0.345, 0.955),
    "AS": ("American Samoa", 0.455, 0.905),
    "VI": ("U.S. Virgin Is.", 0.455, 0.955),
}
PROJECTORS = {"AK": ALASKA, "HI": HAWAII, "PR": PUERTO_RICO}


def _iter_rings(geom):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def _bounds(features, project):
    xs, ys = [], []
    for feat in features:
        for ring in _iter_rings(feat["geometry"]):
            for lon, lat in ring:
                x, y = project(lon, lat)
                xs.append(x)
                ys.append(y)
    return min(xs), max(xs), min(ys), max(ys)


class _Fit:
    """Scale and offset mapping one region's projected units into a box."""

    def __init__(self, project, features, box):
        self.project = project
        minx, maxx, miny, maxy = _bounds(features, project)
        bx, by, bw, bh = box
        # Fit inside the box without distorting: one scale for both axes.
        self.scale = min(bw / (maxx - minx), bh / (maxy - miny))
        self.tx = bx + (bw - (maxx - minx) * self.scale) / 2 - minx * self.scale
        self.ty = by + (bh - (maxy - miny) * self.scale) / 2 - miny * self.scale

    def __call__(self, lon, lat):
        x, y = self.project(lon, lat)
        return x * self.scale + self.tx, y * self.scale + self.ty


class MapProjection:
    """Composite US map: lower 48 in the frame, AK/HI/PR as insets."""

    def __init__(self, geojson_path, width=940, pad=8):
        with open(geojson_path) as f:
            data = json.load(f)
        by_name = {}
        for feat in data["features"]:
            by_name.setdefault(feat["properties"].get("name", ""), []).append(feat)

        self.main_features = [
            f for name, feats in by_name.items() if name not in INSET_STATES
            for f in feats
        ]
        minx, maxx, miny, maxy = _bounds(self.main_features, LOWER48)
        scale = (width - 2 * pad) / (maxx - minx)
        self.width = width
        self.height = math.ceil((maxy - miny) * scale + 2 * pad)
        self.fits = {
            None: _Fit(LOWER48, self.main_features,
                       (pad, pad, width - 2 * pad, self.height - 2 * pad))
        }
        self.inset_features = {}
        for name, code in INSET_STATES.items():
            feats = by_name.get(name)
            if not feats:
                continue
            bx, by_, bw, bh = INSET_BOXES[code]
            box = (bx * width, by_ * self.height, bw * width, bh * self.height)
            self.fits[code] = _Fit(PROJECTORS[code], feats, box)
            self.inset_features[code] = (name, feats)

        self.territory_points = {
            code: (fx * width, fy * self.height)
            for code, (_label, fx, fy) in TERRITORY_MARKERS.items()
        }

    def to_svg_coords(self, lon, lat, state=None):
        """Project a point through whichever region owns it."""
        if state in self.territory_points:
            return self.territory_points[state]
        return self.fits.get(state if state in self.fits else None)(lon, lat)

    def mappable(self, state):
        """True when a newsroom in this state has somewhere to sit."""
        return state in self.fits or state in self.territory_points or (
            state is not None and state not in INSET_STATES.values()
        )

    def state_paths(self):
        """(name, svg path) for the lower 48 and every inset."""
        for feat in self.main_features:
            yield feat["properties"].get("name", ""), self._path(feat, None)
        for code, (name, feats) in self.inset_features.items():
            for feat in feats:
                yield name, self._path(feat, code)

    def _path(self, feat, code):
        fit = self.fits[code] if code else self.fits[None]
        parts = []
        for ring in _iter_rings(feat["geometry"]):
            # Whole pixels are plenty at this scale, and halve the path data.
            pts = [f"{round(x)},{round(y)}" for x, y in (fit(lon, lat) for lon, lat in ring)]
            parts.append("M" + "L".join(pts) + "Z")
        return "".join(parts)

    def territory_labels(self):
        """(code, label, x, y) for the territories drawn as markers."""
        for code, (label, _fx, _fy) in TERRITORY_MARKERS.items():
            x, y = self.territory_points[code]
            yield code, label, x, y
