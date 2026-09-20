"""The box a reader drags over the map to search one part of the country.

The map is a composite — the lower 48 on one conic, Alaska, Hawaii and
Puerto Rico each fitted into their own box in the ocean — so a rectangle
drawn over it is not a rectangle in latitude and longitude and cannot be
turned into one: a box around the Alaska inset would come out as a patch of
the Pacific, and a box around the whole picture would take in half of
Canada. So the box is kept in the map's own coordinates, as thousandths of
its width and height, and a newsroom is inside the box when its dot is.

That also keeps one rectangle in play rather than three: what the reader
drags, what the URL carries and what the query filters on are the same
numbers, and a search that was narrowed to the Bay Area can be pasted to
somebody else and open on the Bay Area.
"""

from functools import lru_cache

from . import config
from .albers import MapProjection

# Thousandths, not pixels or percent: integers keep the URL short and exact,
# and the map is drawn at whatever width the reader's screen gives it.
SCALE = 1000
WHOLE = (0, 0, SCALE, SCALE)

# A box this small is a mis-drag, not an ask. It is also about the size of
# two fingertips on a phone, which is the smallest thing worth dragging.
MIN_SIDE = 30


def parse(raw):
    """A box from the URL, or None for the whole map.

    None is the default and the answer to anything malformed: a filter
    nobody can see and nobody asked for is worse than no filter, and this
    string arrives from the query string like any other.
    """
    parts = (raw or "").split(",")
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(p) for p in parts)
    except ValueError:
        return None
    x0, x1 = sorted((max(0, min(SCALE, x0)), max(0, min(SCALE, x1))))
    y0, y1 = sorted((max(0, min(SCALE, y0)), max(0, min(SCALE, y1))))
    box = (x0, y0, x1, y1)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE or box == WHOLE:
        return None
    return box


def unparse(box):
    """The box as the URL carries it."""
    return ",".join(str(n) for n in box)


@lru_cache(maxsize=1)
def projection():
    """The same composite the map is drawn with, built once per process."""
    return MapProjection(config.STATES_GEOJSON)


def point(lat, lon, state):
    """Where a newsroom's dot sits on the map, in box coordinates.

    None where the map has nowhere to put it — no coordinates, no state, or
    a state the composite does not draw. Those newsrooms are not on the map
    and so are not in any box drawn over it.
    """
    if lat is None or lon is None:
        return None
    state = (state or "").upper()
    if not state or not projection().mappable(state):
        return None
    proj = projection()
    x, y = proj.to_svg_coords(float(lon), float(lat), state)
    return (x / proj.width * SCALE, y / proj.height * SCALE)


def contains(box, spot):
    """Is this dot inside the box? Edges count as inside."""
    if spot is None:
        return False
    x0, y0, x1, y1 = box
    x, y = spot
    return x0 <= x <= x1 and y0 <= y <= y1


def ids_inside(box, rows):
    """The ids of the newsrooms whose dots the box covers.

    `rows` is (id, lat, lon, state) as the orgs table hands them over.
    """
    return [row[0] for row in rows if contains(box, point(row[1], row[2], row[3]))]
