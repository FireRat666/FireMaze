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
    if bias == 0.5:
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


def _place_stairs(cells_3d: List, width: int, depth: int, floors: int, wall_mode: str, stair_count: int = 1, stair_footprint: str = '1x1', stair_style: str = 'stair', stair_direction: str = 'random') -> List[dict]:
    """Place stair footprints in 3D cells and return placed stair records."""
    placed = []
    rng = get_rng()
    for z in range(floors - 1):
        candidates = []
        for y in range(1, depth - 1):
            for x in range(1, width - 1):
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


def _expand_cells_to_3d(cells_2d: List, width: int, depth: int, floors: int, wall_mode: str, stair_count: int = 1, stair_footprint: str = '1x1', stair_style: str = 'stair', stair_direction: str = 'random') -> Tuple[List, List]:
    """Clone 2D cells to 3D [floors][depth][width] and carve stair footprints."""
    if floors <= 1:
        return [cells_2d], []
    cells_3d = [cells_2d]
    for _ in range(1, floors):
        cells_3d.append(copy.deepcopy(cells_2d))
    stairs_placed = _place_stairs(
        cells_3d, width, depth, floors, wall_mode,
        stair_count=stair_count, stair_footprint=stair_footprint,
        stair_style=stair_style, stair_direction=stair_direction
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
            
            idx = (py * img_w + px) * 4
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
