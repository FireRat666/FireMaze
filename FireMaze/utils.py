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

