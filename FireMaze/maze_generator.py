"""Maze generation facade for FireMaze.

Re-exports core data structures, pathfinding functions, and delegates maze
generation to modular sub-algorithms.
"""

from typing import List, Tuple, Optional
from .maze_data import MazeData
from .pathfinder import find_shortest_path  # re-exported for operators.py; sub-algorithms import directly from pathfinder
from .maze_algorithms.polar_maze import generate_polar_maze
from .maze_algorithms.cube_maze import _generate_cube_maze
from .maze_algorithms.thin_maze import _generate_thin_maze
from .utils import set_seed

def generate_maze(
    width: int,
    depth: int,
    seed: Optional[int] = None,
    mode: str = 'center',
    emergency_exits: bool = False,
    algorithm: str = 'dfs',
    rooms_enable: bool = False,
    rooms_count: int = 3,
    min_room_size: int = 2,
    max_room_size: int = 4,
    loop_probability: float = 0.0,
    isolated_wall_prob: float = 0.0,
    entrance_side: str = 'ANY',
    exit_side: str = 'ANY',
    num_entrances: int = 1,
    num_exits: int = 1,
    wall_mode: str = 'thin',
    num_wall_meshes: int = 0,
    num_floor_meshes: int = 0,
    num_roof_meshes: int = 0,
    mask_image = None,
    mask_invert: bool = False,
    grid_type: str = 'rect',
    polar_rings: int = 5,
    floors: int = 1,
    stair_footprint: str = '1x1',
    stair_style: str = 'stair',
    stair_count: int = 1,
    stair_direction: str = 'random',
    selection_bias: float = 0.5,
    straightness: float = 0.5,
    direction_bias: float = 0.5,
    east_bias: float = 0.5,
    orientation_bias: float = 0.5,
    passage_bias: float = 0.5,
    eller_merge_prob: float = 0.5,
    radial_bias: float = 0.5,
) -> MazeData:
    """Generate a complete rectangular or polar maze with the selected algorithm."""
    # Coordinate global and shared PRNG seeds
    set_seed(seed)

    # Disable mask for multilevel mazes
    if floors > 1 and mask_image is not None:
        mask_image = None

    if grid_type == 'polar':
        return generate_polar_maze(
            rings=polar_rings,
            seed=seed,
            algorithm=algorithm,
            mode=mode,
            wall_mode=wall_mode,
            num_wall_meshes=num_wall_meshes,
            num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes,
            floors=floors,
            stair_footprint=stair_footprint,
            stair_style=stair_style,
            stair_count=stair_count,
            stair_direction=stair_direction,
            radial_bias=radial_bias,
        )

    if wall_mode == 'cube':
        return _generate_cube_maze(
            width=width, depth=depth, seed=seed, mode=mode,
            emergency_exits=emergency_exits, algorithm=algorithm,
            rooms_enable=rooms_enable, rooms_count=rooms_count,
            min_room_size=min_room_size, max_room_size=max_room_size,
            loop_probability=loop_probability, isolated_wall_prob=isolated_wall_prob,
            entrance_side=entrance_side, exit_side=exit_side,
            num_entrances=num_entrances, num_exits=num_exits,
            num_wall_meshes=num_wall_meshes, num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes, mask_image=mask_image,
            mask_invert=mask_invert, floors=floors, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_count=stair_count, stair_direction=stair_direction,
            selection_bias=selection_bias, straightness=straightness,
            direction_bias=direction_bias, east_bias=east_bias,
            orientation_bias=orientation_bias, passage_bias=passage_bias,
            eller_merge_prob=eller_merge_prob
        )
    elif wall_mode == 'thin':
        return _generate_thin_maze(
            width=width, depth=depth, seed=seed, mode=mode,
            emergency_exits=emergency_exits, algorithm=algorithm,
            rooms_enable=rooms_enable, rooms_count=rooms_count,
            min_room_size=min_room_size, max_room_size=max_room_size,
            loop_probability=loop_probability, isolated_wall_prob=isolated_wall_prob,
            entrance_side=entrance_side, exit_side=exit_side,
            num_entrances=num_entrances, num_exits=num_exits,
            num_wall_meshes=num_wall_meshes, num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes, mask_image=mask_image,
            mask_invert=mask_invert, floors=floors, stair_footprint=stair_footprint,
            stair_style=stair_style, stair_count=stair_count, stair_direction=stair_direction,
            selection_bias=selection_bias, straightness=straightness,
            direction_bias=direction_bias, east_bias=east_bias,
            orientation_bias=orientation_bias, passage_bias=passage_bias,
            eller_merge_prob=eller_merge_prob
        )
    else:
        raise ValueError(f"Unknown wall_mode: {wall_mode}")
