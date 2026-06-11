"""Shared utility functions for FireMaze addon."""


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
        cells = cells.get("cells", cells)
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


import random

# Shared PRNG instance
shared_rng = random.Random()

def get_rng():
    """Get the shared random instance."""
    return shared_rng

def set_seed(seed):
    """Set seed for the shared random instance and global random state."""
    if seed:
        shared_rng.seed(seed)
        random.seed(seed)
    else:
        import time
        s = int(time.time() * 1000)
        shared_rng.seed(s)
        random.seed(s)


def get_cell_id(z: int, y: int, x: int) -> int:
    """Encode 3D coordinates into a single unique integer cell ID."""
    return z * 1000000 + y * 1000 + x


def decode_cell_id(cell_id: int) -> tuple:
    """Decode a unique integer cell ID back into 3D coordinates (z, y, x)."""
    z = cell_id // 1000000
    rem = cell_id % 1000000
    y = rem // 1000
    x = rem % 1000
    return z, y, x




