"""Maze data structures for FireMaze."""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional

@dataclass
class MazeData:
    """Container for all maze data: dimensions, cells, entrance/exits, guide path, grid type, stairs."""

    width: int
    depth: int
    cells: List  # cells[z][y][x] = [...] (3D for multilevel, 2D for single-floor back-compat)
    entrance: Optional[Tuple[int, int, str]] = None
    exits: List[Tuple[int, int, str]] = field(default_factory=list)
    center: Tuple[int, int] = (0, 0)
    guide_path: List = field(default_factory=list)  # (z, y, x) 3-tuples or (y, x) 2-tuples back-compat
    grid_type: str = 'rect'
    polar_rings: int = 0
    ring_sectors: List[int] = field(default_factory=list)
    floors: int = 1
    stairs: List[dict] = field(default_factory=list)


class UnionFind:
    """Disjoint-set data structure (union-find) with path compression."""

    def __init__(self, size: int):
        """Initialize parent array with each element pointing to itself."""
        self.parent = list(range(size))

    def find(self, i: int) -> int:
        """Find the root representative of element i with path compression."""
        path = []
        while self.parent[i] != i:
            path.append(i)
            i = self.parent[i]
        for node in path:
            self.parent[node] = i
        return i

    def union(self, i: int, j: int) -> bool:
        """Union the sets containing i and j. Return True if a merge occurred."""
        root_i = self.find(i)
        root_j = self.find(j)
        if root_i != root_j:
            self.parent[root_i] = root_j
            return True
        return False
