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


def _inside_diamond_strict(u: float, v: float) -> bool:
    return abs(u - 0.5) + abs(v - 0.5) < 0.5


def _inside_triangle_strict(u: float, v: float) -> bool:
    verts = _triangle_vertices()
    return _point_in_polygon(u, v, verts) and not is_point_on_polygon_boundary(u, v, verts)


def _inside_hexagon_strict(u: float, v: float) -> bool:
    verts = _hexagon_vertices()
    return _point_in_polygon(u, v, verts) and not is_point_on_polygon_boundary(u, v, verts)


# Dispatch tables
_SHAPE_TESTS = {
    'diamond': _inside_diamond,
    'triangle': _inside_triangle,
    'hexagon': _inside_hexagon,
}

_SHAPE_TESTS_STRICT = {
    'diamond': _inside_diamond_strict,
    'triangle': _inside_triangle_strict,
    'hexagon': _inside_hexagon_strict,
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


_perfect_poly_cache = {}

def get_perfect_shape_polygon(shape: str, rotation: str = '0', width: int = 1, depth: int = 1, offset: float = 0.0) -> List[Tuple[float, float]]:
    """Return the normalized vertices of the perfect mathematical shape polygon, possibly offset."""
    key = (shape, rotation, width, depth, offset)
    if key in _perfect_poly_cache:
        return _perfect_poly_cache[key]
        
    if shape == 'triangle':
        raw_verts = _triangle_vertices()
    elif shape == 'hexagon':
        raw_verts = _hexagon_vertices()
    elif shape == 'diamond':
        raw_verts = [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)]
    else:
        _perfect_poly_cache[key] = []
        return []
        
    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)
    poly = [_rotate_point(u, v, angle_rad) for u, v in raw_verts]
    
    if offset != 0.0:
        offset_poly = []
        for u, v in poly:
            du_cells = (u - 0.5) * width
            dv_cells = (v - 0.5) * depth
            L_cells = math.hypot(du_cells, dv_cells)
            if L_cells > 1e-9:
                scale = (L_cells + offset) / L_cells
                u_new = 0.5 + (u - 0.5) * scale
                v_new = 0.5 + (v - 0.5) * scale
                offset_poly.append((u_new, v_new))
            else:
                offset_poly.append((u, v))
        poly = offset_poly
        
    _perfect_poly_cache[key] = poly
    return poly


_segmented_poly_cache = {}


def get_segmented_boundary_polygon(
    width: int,
    depth: int,
    shape: str,
    rotation: str = '0',
) -> List[Tuple[float, float]]:
    """Compute and cache the normalized segmented boundary polygon vertices."""
    key = (width, depth, shape, rotation)
    if key in _segmented_poly_cache:
        return _segmented_poly_cache[key]

    test_fn = _SHAPE_TESTS_STRICT.get(shape)
    if test_fn is None:
        _segmented_poly_cache[key] = []
        return []

    angle_rad = _ROTATION_ANGLES.get(rotation, 0.0)

    def _pt_outside(u: float, v: float) -> bool:
        pu, pv = u, v
        if angle_rad != 0.0:
            pu, pv = _rotate_point(pu, pv, -angle_rad)
        return not test_fn(pu, pv)

    sb = get_shape_mask(width, depth, shape, rotation)
    corner_dict = {}
    for cy in range(depth):
        for cx in range(width):
            if sb[cy][cx]:
                continue
            boundary = False
            for ny, nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if nx < 0 or nx >= width or ny < 0 or ny >= depth or sb[ny][nx]:
                    boundary = True
                    break
            if not boundary:
                continue
            for cu, cv in [(cx, cy), (cx+1, cy), (cx+1, cy+1), (cx, cy+1)]:
                u, v = cu / width, cv / depth
                if _pt_outside(u, v):
                    c_key = (cu, cv)
                    corner_dict.setdefault(c_key, set()).add((cx, cy))

    if not corner_dict:
        _segmented_poly_cache[key] = []
        return []

    corner_items = list(corner_dict.keys())
    cx_center = 0.5
    cy_center = 0.5
    corner_items.sort(key=lambda c: math.atan2((c[1] / depth) - cy_center, (c[0] / width) - cx_center))
    
    poly = [(c[0] / width, c[1] / depth) for c in corner_items]
    _segmented_poly_cache[key] = poly
    return poly


def is_point_on_polygon_boundary(u: float, v: float, polygon: List[Tuple[float, float]], tol: float = 1e-5) -> bool:
    """Return True if point (u, v) lies on the boundary or vertices of the polygon."""
    n = len(polygon)
    for i in range(n):
        p1x, p1y = polygon[i]
        p2x, p2y = polygon[(i + 1) % n]
        dx = p2x - p1x
        dy = p2y - p1y
        if dx == 0.0 and dy == 0.0:
            if math.hypot(u - p1x, v - p1y) < tol:
                return True
        else:
            t = ((u - p1x) * dx + (v - p1y) * dy) / (dx * dx + dy * dy)
            if -tol <= t <= 1.0 + tol:
                t_clamped = max(0.0, min(1.0, t))
                qx = p1x + t_clamped * dx
                qy = p1y + t_clamped * dy
                if math.hypot(u - qx, v - qy) < tol:
                    return True
    return False


def clip_cell(
    x: int,
    y: int,
    width: int,
    depth: int,
    ts: float,
    shape: str,
    rotation: str = '0',
) -> Optional[Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]]:
    """Clip cell (x, y) to the segmented shape boundary wall contour.

    Returns:
        (verts_3d, tri_indices): Vertices and triangle faces in world space,
                                 or None if cell is fully inside or fully outside.
    """
    if shape == 'rect':
        return None

    test_fn = _SHAPE_TESTS.get(shape)
    if test_fn is None:
        return None

    segmented_poly = get_segmented_boundary_polygon(width, depth, shape, rotation)
    if not segmented_poly:
        return None

    def is_inside_poly(u: float, v: float) -> bool:
        return (_point_in_polygon(u, v, segmented_poly) or
                is_point_on_polygon_boundary(u, v, segmented_poly))

    # 4 cell corners in normalized space
    corners = [
        (x / width, y / depth),          # BL (0)
        ((x + 1) / width, y / depth),    # BR (1)
        ((x + 1) / width, (y + 1) / depth), # TR (2)
        (x / width, (y + 1) / depth),    # TL (3)
    ]

    inside = [is_inside_poly(u, v) for u, v in corners]
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
                poly_verts.append(_intersect_segment(p1, p2, is_inside_poly))
        else:
            if in2:
                # Outside to Inside crossing
                poly_verts.append(_intersect_segment(p2, p1, is_inside_poly))

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


def _clip_segment_to_box(
    ax: float, ay: float, bx: float, by: float,
    xmin: float, xmax: float, ymin: float, ymax: float
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Clip directed line segment A -> B to the 2D box using Liang-Barsky."""
    t0, t1 = 0.0, 1.0
    dx = bx - ax
    dy = by - ay

    def clip_test(p: float, q: float) -> bool:
        nonlocal t0, t1
        if p > 0.0:
            t = q / p
            if t < t0:
                return False
            if t < t1:
                t1 = t
        elif p < 0.0:
            t = q / p
            if t > t1:
                return False
            if t > t0:
                t0 = t
        else:
            if q < 0.0:
                return False
        return True

    # Left:   -dx * t <= ax - xmin
    if not clip_test(-dx, ax - xmin):
        return None
    # Right:  dx * t <= xmax - ax
    if not clip_test(dx, xmax - ax):
        return None
    # Bottom: -dy * t <= ay - ymin
    if not clip_test(-dy, ay - ymin):
        return None
    # Top:    dy * t <= ymax - ay
    if not clip_test(dy, ymax - ay):
        return None

    if t0 + 1e-6 < t1:
        p0 = (ax + t0 * dx, ay + t0 * dy)
        p1 = (ax + t1 * dx, ay + t1 * dy)
        return p0, p1
    return None


def get_boundary_edges(
    x: int,
    y: int,
    width: int,
    depth: int,
    ts: float,
    shape: str,
    rotation: str = '0',
    offset: float = 0.0,
) -> List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """Return the world-space edge segment(s) representing the shape boundary contour inside cell (x, y).

    Returns a list of (v0, v1) tuples of 3D coordinates.
    """
    if shape == 'rect':
        return []

    poly = get_perfect_shape_polygon(shape, rotation, width, depth, offset)
    if not poly:
        return []

    # Convert normalized vertices to world space
    world_poly = [(u * width * ts, v * depth * ts) for u, v in poly]

    xmin = x * ts
    xmax = (x + 1) * ts
    ymin = y * ts
    ymax = (y + 1) * ts

    edges = []
    n = len(world_poly)
    for i in range(n):
        p0 = world_poly[i]
        p1 = world_poly[(i + 1) % n]
        
        clipped = _clip_segment_to_box(p0[0], p0[1], p1[0], p1[1], xmin, xmax, ymin, ymax)
        if clipped is not None:
            v0 = (clipped[0][0], clipped[0][1], 0.0)
            v1 = (clipped[1][0], clipped[1][1], 0.0)
            edges.append((v0, v1))
    return edges


def get_cell_clip_status(x: int, y: int, width: int, depth: int, shape: str, rotation: str, offset: float) -> str:
    """Determine if a cell is completely inside, intersected (clip), or completely outside the shape boundary."""
    if shape == 'rect':
        return 'full'

    poly = get_perfect_shape_polygon(shape, rotation, width, depth, offset)
    if not poly:
        return 'none'

    # Check the 4 corners of the cell in normalized space
    corners = [
        (x / width, y / depth),
        ((x + 1) / width, y / depth),
        ((x + 1) / width, (y + 1) / depth),
        (x / width, (y + 1) / depth)
    ]

    inside_count = 0
    for u, v in corners:
        if _point_in_polygon(u, v, poly) or is_point_on_polygon_boundary(u, v, poly):
            inside_count += 1

    if inside_count == 4:
        return 'full'
    elif inside_count > 0:
        return 'clip'
    else:
        return 'none'
