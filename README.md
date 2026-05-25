# FireMaze

A Blender 4.2+ extension for generating random tile-based mazes with two wall construction modes, custom mesh support, and full UV mapping.

## Features

- **DFS iterative backtracking** — generates a perfect maze (exactly one path between any two points) every time
- **Two wall modes** — Thin (configurable-thickness boxes on grid lines) and Cube (full `(2W+1)×(2H+1)` tile grid with same-sized tiles, no overlap)
- **Two completion goals** — Find Center (edge entrance to center) and Find Exit (left edge to right edge)
- **Emergency exits** — optional extra outer-edge openings in Center mode
- **Custom tile meshes** — replace floor, roof, north/south/east/west wall faces, and roof with your own meshes. UV coordinates are transferred automatically
- **Consistent UV orientation** — all faces map U horizontally and V vertically for seamless texture tiling
- **Auto materials** — distinct Principled BSDF materials per object category (floor, wall, roof)
- **Seeded randomness** — repeatable mazes from a fixed seed

## Installation

FireMaze is structured as a Blender 4.2 extension (`.blender_manifest.toml`).

1. Open Blender → Edit → Preferences → Get Extensions
2. Click the dropdown arrow (▼) → **Install from Disk...**
3. Select the `FireMaze/` folder or a zipped copy
4. Enable the addon in the Preferences

Alternatively, copy or symlink the `FireMaze/` folder into Blender's `scripts/addons/` directory and enable it manually.

The panel appears in the 3D Viewport sidebar under the **FireRat** tab.

## Quick Start

1. Open the 3D Viewport sidebar (**N** key) and find the **FireRat** tab
2. Click **Generate Maze** — a maze is created at the 3D cursor with three objects: `FireMaze_Floor`, `FireMaze_Walls`, `FireMaze_Roof`
3. Click **Clear Maze** to remove all generated objects and collections

## Settings

| Setting | Description |
| --- | --- |
| **Width / Depth** | Number of cells along X / Y (3–200) |
| **Wall Height** | Height of the wall geometry |
| **Wall Mode** | **Thin** — narrow boxes on grid lines with configurable thickness; **Cube** — full tile blocks at every wall position |
| **Wall Thickness** | (Thin mode only) Width of each wall segment |
| **Tile Size** | Side length of each square tile |
| **Mode** | **Find Center** — the entrance is on one edge, goal is the center cell; **Find Exit** — entrance on left, exit on right |
| **Emergency Exits** | (Center mode) Adds extra openings on outer edges |
| **Seed** | Random seed (0 = random each time) |
| **Tiles Centered** | Enable if custom meshes are centered at the origin (default Blender primitive behavior). Disable for meshes with bottom-left corner at origin |

## Custom Tiles

FireMaze accepts any mesh object as a tile replacement for floors, walls (four directions separately), and roofs.

### How it works

- **Floor / Roof** — the mesh is translated to each tile position. No rotation or scaling.
- **Wall faces** — the mesh is rotated to stand vertically at the correct wall position and orientation. UVs are swapped on X-facing walls so the texture always runs U horizontally and V vertically.
- **No scaling** is applied — a 1×1 mesh stays 1×1. If your custom wall mesh needs to span the full wall height, provide it at the correct size.

### Tile origin

- **Tiles Centered ON** (default): the mesh origin is at its center, spanning `[-ts/2, ts/2]` in X and Y. This is how Blender primitives (grid, plane, etc.) are created.
- **Tiles Centered OFF**: the mesh origin is at its bottom-left corner, spanning `[0, ts]` in X and Y.

### Thin mode custom meshes

When custom wall meshes are set in Thin mode, the generated box faces are replaced per-direction. End-caps at segment endpoints are always generated automatically.

## Objects

Each generated maze produces three mesh objects:

| Object | Material | Description |
| --- | --- | --- |
| `FireMaze_Floor` | FireMaze_Floor (dark gray) | All floor tiles |
| `FireMaze_Walls` | FireMaze_Walls (mid gray) | All wall faces (exposed sides only, no internal faces) |
| `FireMaze_Roof` | FireMaze_Roof (light gray) | All roof/ceiling tiles |

All faces use full `[0,1]²` UV mapping for seamless texture tiling.

## Collection management

- Consecutive generations create `FireMaze`, `FireMaze.001`, `FireMaze.002`, etc.
- Clear removes all collections matching `FireMaze*` and their objects.

## License

GNU General Public License v3.0. See `LICENSE`.
