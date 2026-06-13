"""Shared utility functions for FireMaze addon."""

import random

def is_valid_ref(ref):
    """Check if a Blender RNA pointer is valid and has not been deleted."""
    if ref is None:
        return False
    try:
        _ = ref.name
        return True
    except ReferenceError:
        return False


def _resolve_cells_3d(cells):
    """Normalize cells to 3D list format [z][y][x] and return (cells_3d, floors)."""
    if isinstance(cells, dict):
        if "cells" not in cells:
            raise ValueError("dict payload must contain 'cells' key")
        cells = cells["cells"]
        if cells is None or isinstance(cells, str) or not isinstance(cells, (list, tuple)):
            raise ValueError(f"dict payload 'cells' must be a sequence, got {type(cells).__name__}")
    if hasattr(cells, "cells"):
        cells = cells.cells
    if (len(cells) > 0 and isinstance(cells[0], list) and 
        len(cells[0]) > 0 and isinstance(cells[0][0], list) and 
        len(cells[0][0]) > 0 and isinstance(cells[0][0][0], (list, tuple))):
        return cells, len(cells)
    return [cells], 1


def _get_stair_footprint_coords(x, y, footprint, orientation):
    """Return list of (cell_x, cell_y) for a stair footprint.

    Args:
        x: Base cell X.
        y: Base cell Y.
        footprint: '1x1', '1x2', or '2x2'.
        orientation: 'N', 'S', 'E', or 'W'.

    Returns:
        List of (cx, cy) tuples covering the footprint.
    """
    coords = []
    if footprint == '1x1':
        coords.append((x, y))
    elif footprint == '1x2':
        coords.append((x, y))
        if orientation == 'E':
            coords.append((x + 1, y))
        elif orientation == 'W':
            coords.append((x - 1, y))
        elif orientation == 'N':
            coords.append((x, y - 1))
        elif orientation == 'S':
            coords.append((x, y + 1))
    elif footprint == '2x2':
        for dy in range(2):
            for dx in range(2):
                coords.append((x + dx, y + dy))
    return coords


# Shared PRNG instance
shared_rng = random.Random()

def get_rng():
    """Get the shared random instance."""
    return shared_rng

def set_seed(seed):
    """Set seed for the shared random instance."""
    if seed is not None:
        shared_rng.seed(seed)
    else:
        import time
        s = int(time.time() * 1000)
        shared_rng.seed(s)


CELL_AXIS_LIMIT = 1000
_CELL_AXIS_MULTIPLIER = 10 ** len(str(CELL_AXIS_LIMIT - 1))


def get_cell_id(z: int, y: int, x: int) -> int:
    """Encode 3D coordinates into a single unique integer cell ID.

    Coordinates x and y must be within [0, CELL_AXIS_LIMIT-1], and z must be non-negative.
    """
    if not (0 <= x < CELL_AXIS_LIMIT and 0 <= y < CELL_AXIS_LIMIT and z >= 0):
        raise ValueError(f"Coordinates out of bounds for encoding: x={x}, y={y}, z={z}. "
                         f"x and y must be in [0, {CELL_AXIS_LIMIT-1}] and z >= 0.")
    return z * _CELL_AXIS_MULTIPLIER * _CELL_AXIS_MULTIPLIER + y * _CELL_AXIS_MULTIPLIER + x


def decode_cell_id(cell_id: int) -> tuple:
    """Decode a unique integer cell ID back into 3D coordinates (z, y, x).

    The decoded coordinate ranges are: x in [0, CELL_AXIS_LIMIT-1], y in [0, CELL_AXIS_LIMIT-1], and z >= 0.
    """
    if cell_id < 0:
        raise ValueError(f"cell_id must be non-negative, got {cell_id}")
    z = cell_id // (_CELL_AXIS_MULTIPLIER * _CELL_AXIS_MULTIPLIER)
    rem = cell_id % (_CELL_AXIS_MULTIPLIER * _CELL_AXIS_MULTIPLIER)
    y = rem // _CELL_AXIS_MULTIPLIER
    x = rem % _CELL_AXIS_MULTIPLIER
    return z, y, x




