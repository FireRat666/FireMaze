"""Shared helper functions for maze generation algorithms."""

import copy
import logging
from typing import List, Optional, Tuple
from ..utils import get_rng, _get_stair_footprint_coords

logger = logging.getLogger(__name__)

def _biased_choice(length: int, bias: float) -> int:
    """Select a randomized index in range(length) biased by the bias parameter.

    bias = 0.5: uniform.
    bias < 0.5: pushed toward center.
    bias > 0.5: pushed toward edges.
    """
    if length <= 1:
        return 0
    rng = get_rng()
    u = rng.random()
    if abs(bias - 0.5) < 1e-12:
        pass
    elif bias < 0.5:
        p = 0.5 / max(0.01, bias)
        diff = u - 0.5
        sign = 1.0 if diff >= 0 else -1.0
        u = 0.5 + sign * (abs(2.0 * diff) ** p) / 2.0
    else:
        p = 2.0 * (1.0 - bias)
        diff = u - 0.5
        sign = 1.0 if diff >= 0 else -1.0
        u = 0.5 + sign * (abs(2.0 * diff) ** p) / 2.0
    return max(0, min(length - 1, int(u * length)))


def _force_cell_open(cells: List, z: int, y: int, x: int, wall_mode: str) -> None:
    """Force a cell (and its shared wall neighbors) to be fully walkable."""
    depth = len(cells[z])
    width = len(cells[z][0])
    if 0 <= y < depth and 0 <= x < width:
        if wall_mode == 'cube':
            cells[z][y][x][0] = False
        else:
            for i in range(4):
                cells[z][y][x][i] = False
            # Open shared walls with neighbors
            if y + 1 < depth:
                cells[z][y + 1][x][1] = False
            if y - 1 >= 0:
                cells[z][y - 1][x][0] = False
            if x + 1 < width:
                cells[z][y][x + 1][3] = False
            if x - 1 >= 0:
                cells[z][y][x - 1][2] = False


def _place_stairs(cells_3d: List, width: int, depth: int, floors: int, wall_mode: str, stair_count: int = 1, stair_footprint: str = '1x1', stair_style: str = 'stair', stair_direction: str = 'random', blocked: Optional[List[List[bool]]] = None) -> List[dict]:
    """Place stair footprints in 3D cells and return placed stair records."""
    placed = []
    rng = get_rng()
    for z in range(floors - 1):
        candidates = []
        for y in range(1, depth - 1):
            for x in range(1, width - 1):
                if blocked:
                    if wall_mode == 'cube':
                        sub_x = (x - 1) // 2
                        sub_y = (y - 1) // 2
                        if 0 <= sub_y < len(blocked) and 0 <= sub_x < len(blocked[0]) and blocked[sub_y][sub_x]:
                            continue
                    else:
                        if 0 <= y < len(blocked) and 0 <= x < len(blocked[0]) and blocked[y][x]:
                            continue
                src_ok = not cells_3d[z][y][x][0] if wall_mode == 'cube' else True
                dst_ok = not cells_3d[z + 1][y][x][0] if wall_mode == 'cube' else True
                if src_ok and dst_ok:
                    candidates.append((x, y))
        if not candidates:
            continue

        footprint = stair_footprint
        if stair_direction == 'random':
            orientation = rng.choice(['N', 'S', 'E', 'W'])
        else:
            orientation = stair_direction
        num_stairs = max(1, min(stair_count, len(candidates)))

        rng.shuffle(candidates)
        placed_cells = set()  # track footprint cells already used by stairs on this floor
        placed_count = 0
        for sx, sy in candidates:
            if placed_count >= num_stairs:
                break
            fp_coords = _get_stair_footprint_coords(sx, sy, footprint, orientation)
            if any(c[0] <= 0 or c[0] >= width - 1 or c[1] <= 0 or c[1] >= depth - 1 for c in fp_coords):
                continue
            if any(c in placed_cells for c in fp_coords):
                continue
            if blocked:
                footprint_blocked = False
                for cx, cy in fp_coords:
                    if wall_mode == 'cube':
                        sub_cx = (cx - 1) // 2
                        sub_cy = (cy - 1) // 2
                        if 0 <= sub_cy < len(blocked) and 0 <= sub_cx < len(blocked[0]) and blocked[sub_cy][sub_cx]:
                            footprint_blocked = True
                            break
                    else:
                        if 0 <= cy < len(blocked) and 0 <= cx < len(blocked[0]) and blocked[cy][cx]:
                            footprint_blocked = True
                            break
                if footprint_blocked:
                    continue

            for c in fp_coords:
                placed_cells.add(c)
                _force_cell_open(cells_3d, z, c[1], c[0], wall_mode)
                _force_cell_open(cells_3d, z + 1, c[1], c[0], wall_mode)

            rec_type = 'ramp' if stair_style in ('ramp', 'rectangular') else 'stair'
            rec = {
                'z': z,
                'x': sx,
                'y': sy,
                'type': rec_type,
                'footprint': footprint,
                'orientation': orientation,
            }
            placed.append(rec)
            placed_count += 1
    return placed


def _expand_cells_to_3d(cells_2d: List, width: int, depth: int, floors: int, wall_mode: str, stair_count: int = 1, stair_footprint: str = '1x1', stair_style: str = 'stair', stair_direction: str = 'random', blocked: Optional[List[List[bool]]] = None) -> Tuple[List, List]:
    """Clone 2D cells to 3D [floors][depth][width] and carve stair footprints."""
    if floors <= 1:
        return [cells_2d], []
    cells_3d = [cells_2d]
    for _ in range(1, floors):
        cells_3d.append(copy.deepcopy(cells_2d))
    stairs_placed = _place_stairs(
        cells_3d, width, depth, floors, wall_mode,
        stair_count=stair_count, stair_footprint=stair_footprint,
        stair_style=stair_style, stair_direction=stair_direction,
        blocked=blocked
    )
    return cells_3d, stairs_placed


def _get_image_mask_data(mask_image, invert: bool, width: int, depth: int) -> List[List[bool]]:
    """Sample a mask image at cell centres and return a 2D blocked boolean grid."""
    blocked = [[False] * width for _ in range(depth)]
    if not mask_image:
        return blocked

    img_w, img_h = mask_image.size[0], mask_image.size[1]
    pixels = list(mask_image.pixels)
    if not pixels:
        return blocked

    for y in range(depth):
        for x in range(width):
            px = int(((x + 0.5) / width) * img_w)
            py = int(((y + 0.5) / depth) * img_h)
            px = max(0, min(img_w - 1, px))
            py = max(0, min(img_h - 1, py))

            channels = len(pixels) // (img_w * img_h)
            idx = (py * img_w + px) * channels
            if idx + 2 < len(pixels):
                r = pixels[idx]
                g = pixels[idx + 1]
                b = pixels[idx + 2]
                brightness = 0.299 * r + 0.587 * g + 0.114 * b
                if invert:
                    brightness = 1.0 - brightness
                if brightness < 0.5:
                    blocked[y][x] = True

    return blocked


def _merge_shape_mask(blocked: List[List[bool]], shape_blocked: List[List[bool]]) -> List[List[bool]]:
    """Union a shape mask into the existing blocked array.

    A cell is blocked if it was blocked by either the image mask or the
    shape mask.  Mutates and returns *blocked* for convenience.
    """
    if not shape_blocked:
        return blocked
    sb_h = len(shape_blocked)
    sb_w = len(shape_blocked[0]) if sb_h else 0
    for y in range(len(blocked)):
        for x in range(len(blocked[0])):
            if sb_h == len(blocked) and sb_w == len(blocked[0]):
                if shape_blocked[y][x]:
                    blocked[y][x] = True
            else:
                sy = y * 2 + 1
                sx = x * 2 + 1
                if sy < sb_h and sx < sb_w and shape_blocked[sy][sx]:
                    blocked[y][x] = True
    return blocked


def _get_start_cell(blocked: List[List[bool]], w: int, h: int) -> Optional[Tuple[int, int]]:
    """Find a non-blocked starting cell close to the center, or None if all cells are blocked."""
    cx, cy = w // 2, h // 2
    for r in range(max(w, h)):
        for dx in range(-r, r + 1):
            for ny in (cy - r, cy + r):
                nx = cx + dx
                if 0 <= nx < w and 0 <= ny < h and not blocked[ny][nx]:
                    return nx, ny
        for dy in range(-r + 1, r):
            for nx in (cx - r, cx + r):
                ny = cy + dy
                if 0 <= nx < w and 0 <= ny < h and not blocked[ny][nx]:
                    return nx, ny
    return None


def _get_shape_boundary_candidates(blocked: List[List[bool]], width: int, depth: int) -> List[Tuple[int, int, str]]:
    """Find cells on the shape boundary — inside the shape but adjacent to an outside cell.

    Returns a list of (x, y, direction) tuples where *direction* is the
    outward-facing wall direction (N/S/E/W) toward the nearest blocked
    neighbour.  Only cells that are unblocked and have at least one
    blocked (or out-of-bounds) neighbour are included.
    """
    candidates = []
    dir_deltas = [('N', 0, 1), ('S', 0, -1), ('E', 1, 0), ('W', -1, 0)]
    for y in range(depth):
        for x in range(width):
            if blocked[y][x]:
                continue  # cell itself is outside the shape
            for d, dx, dy in dir_deltas:
                nx, ny = x + dx, y + dy
                if nx < 0 or nx >= width or ny < 0 or ny >= depth or blocked[ny][nx]:
                    candidates.append((x, y, d))
    return candidates
