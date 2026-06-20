"""Shape boundary math for FireMaze.

Provides functions to compute cell masks and boundary vertices for
non-rectangular maze shapes (diamond, triangle, hexagon).  Shape math
operates in normalized [0,1]×[0,1] space, then tests cell centres to
produce a ``blocked[y][x]`` boolean grid compatible with the existing
image-mask pipeline.
"""

import math
from typing import List, Tuple, Optional

# ---------------------------------------------------------------------------
# Rotation helpers
# ---------------------------------------------------------------------------

# Enum-style rotation: 0°, 90°, 180°, 270°
_ROTATION_ANGLES = {
    '0': 0.0,
    '90': math.pi / 2,
    '180': math.pi,
    '270': 3 * math.pi / 2,
}


def _rotate_point(u: float, v: float, angle_rad: float) -> Tuple[float, float]:
    """Rotate point (u, v) around (0.5, 0.5) by *angle_rad* radians."""
    cu, cv = u - 0.5, v - 0.5
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    ru = cu * cos_a - cv * sin_a + 0.5
    rv = cu * sin_a + cv * cos_a + 0.5
    return ru, rv


# ---------------------------------------------------------------------------
# Canonical shape tests  (normalised [0,1]×[0,1], unrotated)
# ---------------------------------------------------------------------------

def _inside_diamond(u: float, v: float) -> bool:
    """Manhattan-distance diamond: |u − 0.5| + |v − 0.5| ≤ 0.5."""
    return abs(u - 0.5) + abs(v - 0.5) <= 0.5


def _triangle_vertices() -> List[Tuple[float, float]]:
    """Return 3 vertices of an equilateral triangle inscribed in [0,1]²."""
    # Pointing up, centred at (0.5, 0.5).
    cx, cy = 0.5, 0.5
    r = 0.5  # circumradius
    verts = []
    for i in range(3):
        angle = math.pi / 2 + i * 2 * math.pi / 3  # start at top
        verts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return verts


def _hexagon_vertices() -> List[Tuple[float, float]]:
    """Return 6 vertices of a regular flat-top hexagon inscribed in [0,1]²."""
    cx, cy = 0.5, 0.5
    r = 0.5
    verts = []
    for i in range(6):
        angle = i * math.pi / 3  # flat-top: starts at 0°
        verts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return verts


def _point_in_polygon(u: float, v: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > v) != (yj > v)) and (u < (xj - xi) * (v - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _inside_triangle(u: float, v: float) -> bool:
    return _point_in_polygon(u, v, _triangle_vertices())


def _inside_hexagon(u: float, v: float) -> bool:
    return _point_in_polygon(u, v, _hexagon_vertices())


# Dispatch table
_SHAPE_TESTS = {
    'diamond': _inside_diamond,
    'triangle': _inside_triangle,
    'hexagon': _inside_hexagon,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_shape_mask(
    width: int,
    depth: int,
    shape: str,
    rotation: str = '0',
) -> List[List[bool]]:
    """Return a 2-D blocked array for the requested shape.

    Parameters
    ----------
    width, depth : int
        Grid dimensions (cells along X and Y).
    shape : str
        One of ``'rect'``, ``'diamond'``, ``'triangle'``, ``'hexagon'``.
    rotation : str
        One of ``'0'``, ``'90'``, ``'180'``, ``'270'`` (degrees).

    Returns
    -------
    blocked : list[list[bool]]
        ``blocked[y][x]`` is ``True`` when cell (x, y) is **outside** the shape.
    """
    blocked = [[False] * width for _ in range(depth)]

    if shape == 'rect':
        return blocked  # no masking

    test_fn = _SHAPE_TESTS.get(shape)
    if test_fn is None:
        return blocked  # unknown shape → no masking

    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)

    for y in range(depth):
        for x in range(width):
            # Normalised cell centre
            u = (x + 0.5) / width
            v = (y + 0.5) / depth

            # Rotate the test point *backwards* so the shape appears rotated
            if angle_rad != 0.0:
                u, v = _rotate_point(u, v, -angle_rad)

            if not test_fn(u, v):
                blocked[y][x] = True

    return blocked


def get_shape_boundary_verts(
    width: int,
    depth: int,
    shape: str,
    tile_size: float,
    rotation: str = '0',
) -> List[Tuple[float, float]]:
    """Return world-space vertices of the shape contour.

    Intended for future mesh-clipping (Phase 2).  For now it returns the
    canonical polygon vertices scaled to world space.
    """
    if shape == 'rect':
        w = width * tile_size
        d = depth * tile_size
        return [(0, 0), (w, 0), (w, d), (0, d)]

    vert_fn = {
        'diamond': lambda: [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)],
        'triangle': _triangle_vertices,
        'hexagon': _hexagon_vertices,
    }.get(shape)

    if vert_fn is None:
        w = width * tile_size
        d = depth * tile_size
        return [(0, 0), (w, 0), (w, d), (0, d)]

    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)
    raw = vert_fn()
    result = []
    for u, v in raw:
        if angle_rad != 0.0:
            u, v = _rotate_point(u, v, angle_rad)
        result.append((u * width * tile_size, v * depth * tile_size))
    return result


def _intersect_segment(p1: Tuple[float, float], p2: Tuple[float, float], test_fn) -> Tuple[float, float]:
    """Find the exact shape boundary crossing point along segment (p1, p2) via binary search.
    
    p1 is assumed to be inside the shape, and p2 is assumed to be outside the shape.
    """
    low, high = 0.0, 1.0
    u1, v1 = p1
    u2, v2 = p2
    for _ in range(12):
        mid = (low + high) / 2
        um = u1 + mid * (u2 - u1)
        vm = v1 + mid * (v2 - v1)
        if test_fn(um, vm):
            low = mid
        else:
            high = mid
    t = (low + high) / 2
    return (u1 + t * (u2 - u1), v1 + t * (v2 - v1))


def clip_cell(
    x: int,
    y: int,
    width: int,
    depth: int,
    ts: float,
    shape: str,
    rotation: str = '0',
) -> Optional[Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]]:
    """Clip cell (x, y) to the mathematical shape boundary.

    Returns:
        (verts_3d, tri_indices): Vertices and triangle faces in world space,
                                 or None if cell is fully inside or fully outside.
    """
    if shape == 'rect':
        return None

    test_fn = _SHAPE_TESTS.get(shape)
    if test_fn is None:
        return None

    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)

    def is_inside(u: float, v: float) -> bool:
        if angle_rad != 0.0:
            u, v = _rotate_point(u, v, -angle_rad)
        return test_fn(u, v)

    # 4 cell corners in normalized space
    corners = [
        (x / width, y / depth),          # BL (0)
        ((x + 1) / width, y / depth),    # BR (1)
        ((x + 1) / width, (y + 1) / depth), # TR (2)
        (x / width, (y + 1) / depth),    # TL (3)
    ]

    inside = [is_inside(u, v) for u, v in corners]
    num_inside = sum(inside)

    if num_inside == 4 or num_inside == 0:
        return None

    # Construct the clipped polygon
    poly_verts = []
    for i in range(4):
        p1 = corners[i]
        p2 = corners[(i + 1) % 4]
        in1 = inside[i]
        in2 = inside[(i + 1) % 4]

        if in1:
            poly_verts.append(p1)
            if not in2:
                # Inside to Outside crossing
                poly_verts.append(_intersect_segment(p1, p2, is_inside))
        else:
            if in2:
                # Outside to Inside crossing
                poly_verts.append(_intersect_segment(p2, p1, is_inside))

    # Convert to world space
    world_verts = []
    for u, v in poly_verts:
        world_verts.append((u * width * ts, v * depth * ts, 0.0))

    # Fan triangulate
    k = len(world_verts)
    tris = []
    for i in range(1, k - 1):
        tris.append((0, i, i + 1))

    return world_verts, tris


def get_boundary_edges(
    x: int,
    y: int,
    width: int,
    depth: int,
    ts: float,
    shape: str,
    rotation: str = '0',
) -> List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """Return the world-space edge segment(s) representing the shape boundary contour inside cell (x, y).

    Returns a list containing a single (v0, v1) tuple of 3D coordinates, or an empty list if not a boundary cell.
    """
    if shape == 'rect':
        return []

    test_fn = _SHAPE_TESTS.get(shape)
    if test_fn is None:
        return []

    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)

    def is_inside(u: float, v: float) -> bool:
        if angle_rad != 0.0:
            u, v = _rotate_point(u, v, -angle_rad)
        return test_fn(u, v)

    corners = [
        (x / width, y / depth),          # BL
        ((x + 1) / width, y / depth),    # BR
        ((x + 1) / width, (y + 1) / depth), # TR
        (x / width, (y + 1) / depth),    # TL
    ]

    inside = [is_inside(u, v) for u, v in corners]
    num_inside = sum(inside)

    if num_inside == 4 or num_inside == 0:
        return []

    crossings = []
    for i in range(4):
        p1 = corners[i]
        p2 = corners[(i + 1) % 4]
        in1 = inside[i]
        in2 = inside[(i + 1) % 4]
        if in1 != in2:
            if in1:
                # Inside to Outside crossing
                p_cross = _intersect_segment(p1, p2, is_inside)
                crossings.append((p_cross, 'out'))
            else:
                # Outside to Inside crossing
                p_cross = _intersect_segment(p2, p1, is_inside)
                crossings.append((p_cross, 'in'))

    if len(crossings) == 2:
        # Sort crossings so Inside->Outside transition is first to keep shape interior on the left of the edge
        if crossings[0][1] == 'in' and crossings[1][1] == 'out':
            c_out, c_in = crossings[1][0], crossings[0][0]
        else:
            c_out, c_in = crossings[0][0], crossings[1][0]

        # Convert to world space
        v0 = (c_out[0] * width * ts, c_out[1] * depth * ts, 0.0)
        v1 = (c_in[0] * width * ts, c_in[1] * depth * ts, 0.0)
        return [(v0, v1)]

    return []
