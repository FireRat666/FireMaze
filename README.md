# FireMaze

A powerful, feature-rich Blender 4.2+ extension for generating, editing, and customizing tile-based mazes. Supports two construction modes (Thin and Cube), multiple generation algorithms, procedural rooms, loop/pillar settings, custom collection randomization, interactive viewport editing, real-time pathfinding guides, and detailed local transformations.

![main](Mazes.png)

---

## Features & Settings Guide

### 1. Wall Construction Modes

* **Thin Wall Mode**: Walls are rendered as thin box segments aligned along grid lines with customizable thickness. Ideal for classic dungeon layouts.
* **Cube Mode**: Walls are full, tile-sized cubes centered on grid cells. The generated maze respects the exact layout dimensions, and path cell centers are placed perfectly within the grid.

### 2. Advanced Generation Algorithms

FireMaze supports multiple algorithms to generate distinct maze layout architectures:

* **Depth-First Search (DFS)**: Randomized recursive backtracker. Generates a "perfect" maze with long, winding corridors and a single path between any two points.
* **Kruskal's Algorithm**: Randomly merges paths by removing walls without creating loops. Produces shorter corridors and many dead-ends.
* **Eller's Algorithm**: Generates rows sequentially. Extremely memory-efficient, making it ideal for massive mazes. Corridors have a distinct row-by-row structure.
* **Binary Tree**: A simple, fast grid algorithm. Moves cell-by-cell carving north or east. Generates bias patterns with corridors leading north and east.
* **Prim's Algorithm**: Randomized version of Prim's MST. Carves paths outward from a visited set. Yields highly balanced, highly branching layouts with shorter corridors.
* **Hunt-and-Kill**: Randomly walks until trapped, then scans the grid row-by-row to find an unvisited cell with a visited neighbor. Generates long, winding corridors.
* **Sidewinder**: Processes row-by-row, building horizontal runs and randomly choosing vertical exits. Creates distinct horizontal bias with vertical connecting passages.
* **Wilson's Algorithm**: Loops-erased random walk algorithm. Creates a mathematically unbiased Uniform Spanning Tree with highly complex branch systems.
* **Recursive Division**: Recursively subdivides grid fields using vertical and horizontal walls with single doors carved in them. Generates nested rectangular chambers.
* **Growing Tree**: A hybrid generalized frontier algorithm. Uses a mixed selection strategy (50% DFS, 50% Prim's) to create interesting corridor and cluster flows.

### 3. Procedural Rooms

Pre-carve open room areas within the maze.

* **Enable Rooms**: Toggles room generation.
* **Room Count**: The number of rooms to place.
* **Min / Max Room Size**: Bounds for the randomized width and depth of rooms (measured in cells).
* Rooms automatically connect to the corridor network and are guaranteed to contain no stray pillars or internal walls in both Thin and Cube modes.

### 4. Loops & Isolated Obstacles

* **Loop Probability**: Set between `0.0` (perfect maze) and `1.0` (maximum loops). Randomly removes additional walls to create alternative paths, loops, and circular corridors.
* **Isolated Wall Prob**: Set between `0.0` and `1.0`. Places standalone, single-wall columns or pillars in floor regions to act as obstacles.

### 5. Entrances & Exits

* **Completion Goals**:
  * `Find Center`: Edge entrance leading to a goal in the center.
  * `Find Exit`: Entrance on one side, exit on the opposite side.
* **Placement Side**: Place entrances or exits along specific borders (`North`, `South`, `East`, `West`, or `Random/Any`).
* **Counts**: Support for multiple entrances and exits (from 1 to 10). The generator ensures they always connect properly to path corridors, avoiding isolated entrances in Cube Mode.

### 6. Interactive Viewport Editor

You can paint, modify, and customize the maze layout in real time directly from the 3D viewport:

1. Click **Interactive Edit** (or press the button in the Sidebar).
2. Your viewport status bar displays editing shortcuts. Left-click on any cell or wall face to toggle it on/off.
3. The guide path and Blender meshes rebuild automatically in real time.
4. Press `ESC` or `ENTER` (or click **Exit Edit Mode** in the sidebar) to return to normal scene interaction.
*Note: Clicks on the Sidebar (N-panel) are ignored by the editor modal so you can modify materials, view settings, or click buttons without leaving edit mode.*
*Precision Click Target: The editor modal temporarily generates an invisible flat-faced helper mesh (`_FireMaze_Edit_Helper`) and raycasts against it. This makes face classification and grid coordination 100% mathematically exact, preventing clicks on complex/curved custom meshes from misrouting. In **Instanced Pillars (Pillar) Mode**, clicking the top/roof area of a pillar is fully supported for toggling or swapping, even though the roof mesh itself is not generated.*

### 7. Real-Time Guide Paths

Generate and display the shortest route through your maze:

* **Style**: Render the guide as a simple 3D `Curve`, a solid `Tube` mesh, or a flat `Ribbon`.
* **Sine-Wave Animation**: Apply a floating wave animation using customizable `Wave Amplitude` and `Wave Frequency` values.
* **Emission Shader**: The guide path automatically loads a bright neon-green glowing emissive material (`FireMaze_Guide`) to visually stand out in Eevee or Cycles.

### 8. Custom Tiles, Collections, & Independent Face Swapping

Replace standard meshes with randomized objects from collections and customize individual faces:

* **Floor / Roof Meshes & Collections**: Replace standard tiles with a custom mesh or assign collections (`Floor Collection` / `Roof Collection`) containing multiple mesh objects to randomly distribute varied floor and roof tiles.
* **Directional Walls**: Set different custom meshes for North (+Y), South (-Y), East (+X), and West (-X) wall segments.
* **Wall Collection**: Select a Blender Collection containing multiple wall meshes to randomly distribute varied wall segments.
* **Instanced Pillars (Cube Mode)**: Enable **Instanced Pillars** to use whole meshes from your Wall Collection as single pillars/cubes rather than assembling them face-by-face. Roof tile generation is automatically suppressed in this mode because each pillar mesh is assumed to already have its own roof. Clicking or Shift-clicking on the top/roof area of these pillars works seamlessly to toggle or cycle them.
* **Independent Face/Tile Swapping**: In Interactive Edit Mode, `Shift + Left-Clicking` a face cycles its mesh index from the respective collection:
  * In **Cube Mode**, raycast hit normals are used to detect the clicked direction, allowing you to swap North, South, East, and West wall faces, floor tiles, and roof tiles completely independently (when not in Instanced Pillar Mode).
  * In **Thin Wall Mode**, distance-to-edge calculations (`d_N`, `d_S`, `d_E`, `d_W`) are used to precisely target and swap the clicked thin wall segment, floor, or roof. Shared walls between adjacent cells are automatically synchronized to maintain consistent rendering.
* **Mesh Origin & Alignment Requirements**: For proper alignment of custom/collection meshes:
  * **Pillars & Walls (especially Instanced Pillars)**: Meshes MUST be centered horizontally (local X=0, Y=0) and have their bottom aligned vertically with the local origin (local Z=0). If a mesh is centered vertically (origin at the center of the asset, like default Blender cubes), it will sink halfway into the floor (i.e. it will be lower than it should be).
  * **Floors**: Floor meshes should be centered horizontally (local X=0, Y=0) with their top surface aligned vertically at local Z=0.
  * **Roofs**: Roof meshes should be centered horizontally (local X=0, Y=0) with their top surface aligned vertically at local Z=0.
* **Tiles Centered**: Toggles whether your custom assets have their origin at their center (like Blender primitives) or at the bottom-left corner.

### 9. Detailed Transform Offsets

Add variety and organic offsets to standard or custom tiles using local matrix transforms:

* **Wall & Floor Transforms**: Control `Translate`, `Rotate`, and `Scale` vectors independently.
* Transforms are applied relative to each individual tile segment's local center, making it easy to create crumbled walls, tilted floors, and varied block scales.

### 10. Post-Processing, Cleanups & Colliders

* **Single Wall Object**: Merges all wall and cap faces into a single object (`FireMaze_Walls`) to keep the outliner clean.
* **Merge Objects**: Combines floors, walls, roofs, and caps into one single merged mesh (`FireMaze_Merged`).
* **Remove Doubles**: Performs a final vertex weld operation to merge touching corners and stacked tiled wall segments.
* **Generate Lightmap UVs**: Generates a second UV map named "Lightmap" on all final visual mesh objects (such as `FireMaze_Merged` or separate floor/wall/roof meshes) for baking or lightmapping. It offers two unwrapping methods:
  * **Smart UV Project**: Groups adjacent/co-planar faces into contiguous UV islands (recommended for reducing seam-bleeding in game engines).
  * **Lightmap Pack**: Project and pack each face individually (guarantees zero distortion and maximum packing efficiency, but splits every face).
* **Generate Colliders**: Generates simple, flat-faced helper meshes (`FireMaze_Floor_Collider`, `FireMaze_Walls_Collider`, `FireMaze_Roof_Collider`) matching the maze layout for easy game engine integration (hidden in final renders by default). *Note: Roof colliders are generated even when Instanced Pillars (Pillar Mode) is enabled so that they can be used for collision.*
* **Merge Colliders**: Combines all active collider objects into a single unified `FireMaze_Collider` mesh.

---

## Installation

FireMaze is structured as a standard Blender 4.2+ extension.

1. Zip the `FireMaze/` subdirectory to create `FireMaze.zip` (or use the pre-packaged ZIP in this repository).
2. In Blender, navigate to **Edit** → **Preferences** → **Get Extensions**.
3. Click the dropdown arrow (▼) in the top-right corner and choose **Install from Disk...**
4. Select `FireMaze.zip` and click Install.
5. Enable the extension. The interface will appear in the Sidebar (**N** key) under the **FireRat** tab.

---

## Object Categories & Material Slots

When generated, the addon creates separate objects based on your merge configuration:

| Object Name | Material Slot | Description |
| :--- | :--- | :--- |
| `FireMaze_Floor` | `FireMaze_Floor` (Dark gray) | All floor tiles. |
| `FireMaze_Walls` | `FireMaze_Walls` (Mid gray) | Side faces of walls (optimized to remove hidden internal faces). |
| `FireMaze_Roof` | `FireMaze_Roof` (Medium gray) | All roof/ceiling tiles. |
| `FireMaze_WallEndCaps` | `FireMaze_WallEndCaps` (Reddish) | Caps on endpoints in Thin wall mode (merged if Single Wall is on). |
| `FireMaze_Guide` | `FireMaze_Guide` (Neon green) | Neon emissive path curves/ribbons showing the shortest route. |

---

## Collection & Data Management

* Generating a maze creates a new nested collection (e.g. `FireMaze`, `FireMaze.001`, etc.) containing all generated objects.
* The collection stores a serialized JSON custom property named `fire_maze_data`. This property contains the grid dimensions, cell states, entrance/exit coordinates, and wall mode.
* Interactive editing and rebuild operators read this serialized data directly, ensuring that the viewport editor works seamlessly across different files and sessions.
* Clicking **Clear Maze** scans the scene for any object carrying the `fire_maze` tag, unlinks them, and purges empty `FireMaze*` collections.

---

## License

This addon is distributed under the GNU General Public License v3.0. See `LICENSE` for details.
