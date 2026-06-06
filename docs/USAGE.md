# FireMaze — Usage & Management Guide

Details on object layout, collection management, save/load sessions, and image exporting.

## Object Categories & Material Slots

When generated, the addon creates separate objects based on your merge configuration:

| Object Name | Material Slot | Description |
| :--- | :--- | :--- |
| `FireMaze_Floor` | `FireMaze_Floor` (Dark gray) | All floor tiles. |
| `FireMaze_Walls` | `FireMaze_Walls` (Mid-gray) | Side faces of walls (optimized to remove hidden internal faces). |
| `FireMaze_Roof` | `FireMaze_Roof` (Medium gray) | All roof/ceiling tiles. |
| `FireMaze_WallEndCaps` | `FireMaze_WallEndCaps` (Reddish) | Caps on endpoints in Thin wall mode (merged if Single Wall is on). |
| `FireMaze_Stairs` | `FireMaze_Walls` (Mid-gray) | Generated staircase or ramp geometry. |
| `FireMaze_Guide` | `FireMaze_Guide` (Neon green) | Neon emissive path curves/ribbons showing the shortest route. |

## Collection & Data Management

- Generating a maze creates a new nested collection (e.g. `FireMaze`, `FireMaze.001`, etc.) containing all generated objects.
- The collection stores a serialized JSON custom property named `fire_maze_data`. This property contains the grid dimensions, cell states, entrance/exit coordinates, and wall mode.
- Interactive editing and rebuild operators read this serialized data directly, ensuring that the viewport editor works seamlessly across different files and sessions.
- Clicking **Clear Maze** scans the scene for any object carrying the `fire_maze` tag, unlinks them, and purges empty `FireMaze*` collections. Orphaned mesh/curve datablocks are swept from the database to prevent memory leaks.

### Session Management

- **Save Session to Disk**: Exports all maze settings, pointer references, and the full grid layout to a `.json` file via a file dialog. The session includes every property and the serialized cell data. Note that pointer references (meshes, collections, images) are stored by name and will only be restored if matching datablocks already exist in the current .blend; external custom assets are not packed or recreated across different machines.
- **Load Session from Disk**: Opens a `.json` session file, restores all properties (including `mask_invert`, `optimize_colliders_coplanar`, etc.), re-links mesh/collection/image references by name, recreates the collection, and rebuilds the maze layout.
- **Autosave (Crash Recovery)**: The addon automatically saves current settings and maze data to a temporary file on every generation. After a crash or restart, a **Restore Session** button appears in the **Session & Image Management** panel alongside a **Discard** button to dismiss the recovery data.
- **Discard Recovery Data**: Deletes the temporary autosave file from disk and dismisses the recovery warning without loading.

### Image Export

- **Save PNG to Disk**: Exports the maze layout as a black-and-white PNG directly to a file path of your choice (uses Blender's native file save dialog).
- **Create Blender Image**: Generates an in-memory Blender Image datablock (`FireMaze_Layout`) showing walls as black and walkable cells as white. Useful as a reference minimap or for external editing.

### Session & Image Management Panel

A dedicated sidebar panel groups all session and image operations in one place:

- **Session Management** section with side-by-side `Save Session...` / `Load Session...` buttons.
- Conditional **crash recovery warning box** (shows `Restore Session` and `Discard` buttons) that only appears when a leftover autosave file exists from a previous session.
- **Masking & Image Export** section with `Load Mask from Disk...`, mask selection, invert toggle, `Save PNG to Disk...`, and `Create Blender Image` buttons.
