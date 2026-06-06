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


