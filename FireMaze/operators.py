"""Blender operators for the FireMaze addon.

Provides generate, clear, interactive edit, session save/load,
image export and autosave recovery operators.
"""

import bpy
import json
import math
import os
import tempfile
import threading
import uuid
import logging
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from .maze_generator import generate_maze, find_shortest_path, MazeData
from .mesh_builder import build_maze_objects
from .utils import is_valid_ref, _resolve_cells_3d, get_rng

logger = logging.getLogger(__name__)

IDX_FLOOR = 4

def _angle_diff(a, b):
    """Return the absolute angular difference between two radians."""
    diff = (a - b) % (2 * math.pi)
    if diff > math.pi:
        diff -= 2 * math.pi
    return abs(diff)

def _get_polar_coords(hit_x, hit_y, ts, ring_sectors):
    """Convert a world-space hit point to polar (r_idx, theta) cell coordinates."""
    r_hit = math.sqrt(hit_x**2 + hit_y**2)
    phi_hit = math.atan2(hit_y, hit_x)
    if phi_hit < 0:
        phi_hit += 2 * math.pi
    rings = len(ring_sectors)
    r_idx = math.floor(r_hit / ts + 0.5)
    r_idx = max(0, min(r_idx, rings - 1))
    Nr = ring_sectors[r_idx]
    alpha_r = 2 * math.pi / Nr
    theta = math.floor(phi_hit / alpha_r)
    theta = max(0, min(theta, Nr - 1))
    return r_hit, phi_hit, r_idx, alpha_r, theta, Nr


PROP_NAMES = [
    "width", "depth", "wall_height", "wall_height_tiled", "wall_height_tiles",
    "wall_thickness", "tile_size", "wall_mode", "grid_type", "polar_rings",
    "polar_custom_alignment", "mode", "emergency_exits", "seed", "tiles_centered",
    "algorithm", "rooms_enable", "rooms_count", "min_room_size", "max_room_size",
    "loop_probability", "isolated_wall_prob", "entrance_side", "exit_side",
    "num_entrances", "num_exits", "cube_mode_pillar", "generate_guide",
    "guide_type", "guide_width", "guide_height_offset", "guide_wave_amplitude",
    "guide_wave_frequency", "wall_translate", "wall_rotate", "wall_scale",
    "floor_translate", "floor_rotate", "floor_scale",
    "roof_translate", "roof_rotate", "roof_scale", "single_wall_object",

    "merge_objects", "remove_doubles", "generate_lightmap", "lightmap_method",
    "generate_colliders", "merge_colliders", "optimize_colliders_coplanar", "optimize_coplanar", "vertex_paint_enable",
    "vertex_paint_mode", "vertex_paint_intensity", "prop_torch_density", "prop_chest_density",
    "mask_invert", "thin_wall_double_sided", "clean_wall_corners", "fire_maze_collection_name",
    "floors", "stair_footprint", "stair_style", "stair_direction", "edit_floor_level",
    "stair_count", "edit_tool", "edit_roof",
    "selection_bias", "straightness", "direction_bias", "east_bias", "orientation_bias", "passage_bias", "eller_merge_prob", "radial_bias",
    "maze_shape", "shape_rotation", "smooth_shape_edges"
]

POINTER_PROPS = [
    "custom_floor_mesh", "custom_wall_mesh", "custom_roof_mesh",
    "custom_wall_collection", "custom_floor_collection", "custom_roof_collection",
    "prop_torch_mesh", "prop_chest_mesh", "prop_door_mesh", "mask_image",
    "custom_stair_mesh", "custom_ramp_mesh"
]

show_recovery_warning = True
AUTOSAVE_PATH = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
AUTOSAVE_ACK_PATH = os.path.join(tempfile.gettempdir(), "firemaze_autosave_ack.json")

def check_has_autosave():
    """Return True if an autosave file exists that has not been acknowledged."""
    if not os.path.exists(AUTOSAVE_PATH):
        return False
    if os.path.exists(AUTOSAVE_ACK_PATH):
        try:
            with open(AUTOSAVE_PATH, 'r') as f1:
                d1 = json.load(f1)
            with open(AUTOSAVE_ACK_PATH, 'r') as f2:
                d2 = json.load(f2)
            if d1 == d2:
                return False
        except Exception:
            pass  # Ack file doesn't exist yet or is malformed
    return True

_has_autosave_cached = None

def has_autosave():
    global _has_autosave_cached
    if _has_autosave_cached is None:
        _has_autosave_cached = check_has_autosave()
    return _has_autosave_cached

def _get_active_maze_collection(context):
    """Return the active maze collection from the scene, or None."""
    props = context.scene.fire_maze
    col_name = getattr(props, "fire_maze_collection_name", "")
    col = bpy.data.collections.get(col_name) if col_name else None
    if not col:
        candidates = [c for c in bpy.data.collections if "fire_maze_data" in c]
        if len(candidates) == 1:
            col = candidates[0]
        else:
            col = None
    return col


def _find_or_create_maze_collection(base_name):
    """Find an existing maze collection or create a new one with a unique name."""
    col = bpy.data.collections.get(base_name)
    if col:
        has_maze = any(obj.get("fire_maze") for obj in col.objects)
        if has_maze:
            i = 1
            while bpy.data.collections.get(f"{base_name}.{i:03d}"):
                i += 1
            col = bpy.data.collections.new(f"{base_name}.{i:03d}")
    else:
        col = bpy.data.collections.new(base_name)
    return col

def _remove_maze_collections():
    """Remove all collections that contain fire_maze_data."""
    for col in list(bpy.data.collections):
        if "fire_maze_data" not in col:
            continue
        try:
            bpy.data.collections.remove(col)
        except RuntimeError:
            for parent in list(bpy.data.collections):
                try:
                    parent.children.unlink(col)
                except ValueError:
                    pass
            try:
                bpy.data.collections.remove(col)
            except RuntimeError:
                pass

def delete_edit_helper():
    """Remove the _FireMaze_Edit_Helper object and orphaned mesh data."""
    helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
    if helper:
        data = helper.data
        bpy.data.objects.remove(helper, do_unlink=True)
        if data and data.users == 0:
            bpy.data.meshes.remove(data)

def _serialize_session_data(context):
    """Pack all current FireMaze properties and maze JSON into a session dict."""
    props = context.scene.fire_maze
    props_data = {}
    for name in PROP_NAMES:
        val = getattr(props, name, None)
        if val is not None:
            if isinstance(val, (int, float, str, bool)):
                props_data[name] = val
            elif hasattr(val, "copy") or isinstance(val, (list, tuple)):
                props_data[name] = list(val)
    
    for name in POINTER_PROPS:
        try:
            ref = getattr(props, name, None)
            if ref and hasattr(ref, "name") and ref.name:
                props_data[name] = ref.name
        except (ReferenceError, AttributeError):
            pass
            
    maze_json = None
    col = _get_active_maze_collection(context)
    if col and "fire_maze_data" in col:
        maze_json = col["fire_maze_data"]
        
    return {
        "schema_version": 1,
        "properties": props_data,
        "maze_data": maze_json
    }

def _deserialize_session_data(context, data):
    """Apply a previously serialized session dict to the scene and rebuild the maze."""
    schema_version = data.get("schema_version", 0)
    logger.info(f"Deserializing session data (Schema Version: {schema_version})")
    props = context.scene.fire_maze

    properties = data.get("properties", {})
    maze_json = data.get("maze_data")
    
    for name in PROP_NAMES:
        if name in properties:
            val = properties[name]
            if isinstance(val, list):
                val = tuple(val)
            try:
                setattr(props, name, val)
            except Exception as ex:
                logger.warning(f"Failed to set property {name}: {ex}")
                
    for name in POINTER_PROPS:
        if name in properties:
            val = properties[name]
            if not val:
                setattr(props, name, None)
                continue
            ref = None
            if name == "mask_image":
                ref = bpy.data.images.get(val)
            elif name in {"custom_wall_collection", "custom_floor_collection", "custom_roof_collection"}:
                ref = bpy.data.collections.get(val)
            elif name in {"custom_floor_mesh", "custom_wall_mesh", "custom_roof_mesh"}:
                ref = bpy.data.meshes.get(val)
            elif name in {"custom_stair_mesh", "custom_ramp_mesh"}:
                # These are Object pointers (need full transform/origin info), not bare Mesh datablocks
                ref = bpy.data.objects.get(val)
            else:
                ref = bpy.data.objects.get(val)
            setattr(props, name, ref)
                
    if maze_json:
        col_name = getattr(props, "fire_maze_collection_name", "FireMaze")
        if not col_name:
            col_name = "FireMaze"
        col = bpy.data.collections.get(col_name)
        if not col:
            col = _find_or_create_maze_collection(col_name)
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)
        props.fire_maze_collection_name = col.name
        col["fire_maze_data"] = maze_json
        rebuild_maze_from_collection(context, col)

def save_autosave(context):
    """Serialize current session and write it to a temporary autosave file in a background thread."""
    global _has_autosave_cached
    try:
        payload = _serialize_session_data(context)
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        payload_str = json.dumps(payload, indent=2)

        # Thread-safe status sharing: None = running, 'success' = completed, 'error' = failed
        autosave_status = [None]

        def write_worker(path, data_str, status_flag):
            """Write serialized data to a temp file then atomically replace the target."""
            temp_path = path + "." + uuid.uuid4().hex + ".tmp"
            try:
                with open(temp_path, 'w') as f:
                    f.write(data_str)
                try:
                    os.replace(temp_path, path)
                    status_flag[0] = 'success'
                except PermissionError as pe:
                    logger.warning(f"Autosave blocked by OS/antivirus ({pe}); cleaning up temp file.")
                    status_flag[0] = 'error'
                    try:
                        os.remove(temp_path)
                    except Exception as cleanup_err:
                        logger.debug(f"Failed to remove temp autosave file: {cleanup_err}")
                except Exception as ex:
                    logger.warning(f"Autosave replace failed ({ex}); cleaning up temp file.")
                    status_flag[0] = 'error'
                    try:
                        os.remove(temp_path)
                    except Exception as cleanup_err:
                        logger.debug(f"Failed to remove temp autosave file: {cleanup_err}")
            except Exception as e:
                logger.error(f"Background autosave write failed: {e}")
                status_flag[0] = 'error'
                try:
                    os.remove(temp_path)
                except Exception as cleanup_err:
                    logger.debug(f"Failed to remove temp autosave file: {cleanup_err}")

        def poll_autosave_timer():
            """Unregister timer once autosave completes (success or error)."""
            global _has_autosave_cached
            if autosave_status[0] is not None:
                if autosave_status[0] == 'success':
                    _has_autosave_cached = True
                return None
            return 0.1

        bpy.app.timers.register(poll_autosave_timer)
        threading.Thread(target=write_worker, args=(autosave_path, payload_str, autosave_status), daemon=True).start()
    except Exception as e:
        logger.error(f"Failed to initiate autosave: {e}")

def set_other_mazes_visibility(context, visible):
    """Hide or show all maze objects that do not belong to the active editing collection."""
    props = context.scene.fire_maze
    active_col_name = props.fire_maze_collection_name
    for col in bpy.data.collections:
        if "fire_maze_data" in col and col.name != active_col_name:
            for obj in col.objects:
                obj.hide_viewport = not visible

def rebuild_maze_from_collection(context, col):
    """Rebuild all maze objects from the JSON data stored on a collection."""
    if "fire_maze_data" not in col:
        return
        
    data_dict = json.loads(col["fire_maze_data"])
    wall_mode = data_dict.get('wall_mode', context.scene.fire_maze.wall_mode)
    props = context.scene.fire_maze

    if 'maze_shape' in data_dict:
        props.maze_shape = data_dict['maze_shape']
    if 'shape_rotation' in data_dict:
        props.shape_rotation = data_dict['shape_rotation']
    if 'smooth_shape_edges' in data_dict:
        props.smooth_shape_edges = data_dict['smooth_shape_edges']

    # Determine number of meshes in collections
    num_wall_meshes = 0
    if is_valid_ref(props.custom_wall_collection):
        num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

    num_floor_meshes = 0
    if is_valid_ref(props.custom_floor_collection):
        num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

    num_roof_meshes = 0
    if is_valid_ref(props.custom_roof_collection):
        num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])
    
    grid_type = data_dict.get('grid_type')
    if not grid_type:
        grid_type = 'polar' if 'ring_sectors' in data_dict else 'rect'
    polar_rings = data_dict.get('polar_rings', 0)
    ring_sectors = data_dict.get('ring_sectors', [])

    floors = data_dict.get('floors', 1)
    stairs = data_dict.get('stairs', [])
    stair_count = data_dict.get('stair_count', props.stair_count)
    props.stair_count = stair_count

    if grid_type == 'polar':
        if not isinstance(polar_rings, int) or polar_rings <= 0:
            logger.error("rebuild_maze_from_collection: invalid polar_rings — aborting rebuild")
            return
        if not isinstance(ring_sectors, (list, tuple)) or len(ring_sectors) < polar_rings:
            logger.error("rebuild_maze_from_collection: invalid ring_sectors — aborting rebuild")
            return
        if not all(isinstance(s, int) and s > 0 for s in ring_sectors[:polar_rings]):
            logger.error("rebuild_maze_from_collection: invalid sector counts in ring_sectors — aborting rebuild")
            return

    required_keys = {'width', 'depth', 'cells', 'center'}
    missing = [k for k in required_keys if k not in data_dict]
    if missing:
        logger.error(f"rebuild_maze_from_collection: missing key(s) {missing} in stored JSON — aborting rebuild")
        return
    maze_data = MazeData(
        width=data_dict['width'],
        depth=data_dict['depth'],
        cells=data_dict['cells'],
        entrance=tuple(data_dict['entrance']) if data_dict.get('entrance') else None,
        exits=[tuple(e) for e in data_dict.get('exits', [])],
        center=tuple(data_dict['center']),
        guide_path=[tuple(gp) for gp in data_dict.get('guide_path', [])],
        grid_type=grid_type,
        polar_rings=polar_rings,
        ring_sectors=ring_sectors,
        floors=floors,
        stairs=stairs,
    )

    # Recompute guide path
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode=wall_mode)

    col["fire_maze_data"] = json.dumps({
        'width': maze_data.width,
        'depth': maze_data.depth,
        'cells': maze_data.cells,
        'entrance': maze_data.entrance,
        'exits': maze_data.exits,
        'center': maze_data.center,
        'guide_path': maze_data.guide_path,
        'wall_mode': wall_mode,
        'num_wall_meshes': num_wall_meshes,
        'num_floor_meshes': num_floor_meshes,
        'num_roof_meshes': num_roof_meshes,
        'grid_type': grid_type,
        'polar_rings': polar_rings,
        'ring_sectors': ring_sectors,
        'floors': floors,
        'stairs': stairs,
        'stair_count': stair_count,
        'maze_shape': props.maze_shape,
        'shape_rotation': props.shape_rotation,
        'smooth_shape_edges': props.smooth_shape_edges,
        'schema_version': 1,
    })


    save_autosave(context)

    # Clear old maze objects from this collection and sweep orphans
    _remove_firemaze_objects(col.objects, sweep=True)

    # Rebuild
    props = context.scene.fire_maze
    build_maze_objects(props, maze_data, context, collection=col)
    
    # If colliders are enabled, rebuild them too
    if props.generate_colliders and not props.is_editing:
        build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_Collider")

    # If is_editing is True, rebuild the edit helper!
    if props.is_editing:
        build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_EditHelper")

def _raycast_from_mouse(context, event):
    """Cast a ray from the mouse cursor into the scene and return (object, location, normal)."""
    scene = context.scene
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None or region.type != 'WINDOW':
        return None, None, None

    coord = event.mouse_region_x, event.mouse_region_y
    try:
        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    except Exception as e:
        logger.debug(f"Failed to compute ray from screen coordinates: {e}")
        return None, None, None
    
    depsgraph = context.evaluated_depsgraph_get()
    result, location, normal, index, object, matrix = scene.ray_cast(depsgraph, ray_origin, view_vector)
    
    if result:
        return object, location, normal
    return None, None, None


def _remove_firemaze_objects(objects, sweep=True):
    """Remove fire_maze-tagged objects from an iterable and optionally sweep orphaned datablocks.

    Returns:
        Number of objects removed.
    """
    count = 0
    for obj in list(objects):
        if obj.get("fire_maze"):
            data = obj.data
            obj_type = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if obj_type == 'MESH':
                    try:
                        bpy.data.meshes.remove(data)
                    except Exception as e:
                        logger.debug(f"Failed to remove mesh data: {e}")
                elif obj_type == 'CURVE':
                    try:
                        bpy.data.curves.remove(data)
                    except Exception as e:
                        logger.debug(f"Failed to remove curve data: {e}")
            count += 1
    if sweep:
        for m in list(bpy.data.meshes):
            if m.name.startswith("FireMaze") and m.users == 0:
                try:
                    bpy.data.meshes.remove(m)
                except Exception as e:
                    logger.debug(f"Failed to remove orphaned FireMaze mesh: {e}")
        for c in list(bpy.data.curves):
            if c.name.startswith("FireMaze") and c.users == 0:
                try:
                    bpy.data.curves.remove(c)
                except Exception as e:
                    logger.debug(f"Failed to remove orphaned FireMaze curve: {e}")
    return count


class MAZE_OT_generate(bpy.types.Operator):
    """Generate a new maze from current settings and build its mesh objects."""

    bl_idname = "fire_maze.generate"
    bl_label = "Generate Maze"
    bl_description = "Generate a new maze from current settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """Generate the maze data, create a collection, build meshes, and save autosave."""
        global show_recovery_warning
        show_recovery_warning = False
        props = context.scene.fire_maze

        if props.grid_type == 'rect':
            if props.width < 3 or props.depth < 3:
                self.report({'ERROR'}, "Width and Depth must be at least 3")
                return {'CANCELLED'}
        else:
            if props.polar_rings < 2:
                self.report({'ERROR'}, "Rings must be at least 2")
                return {'CANCELLED'}

        # Determine number of meshes in collections
        num_wall_meshes = 0
        if is_valid_ref(props.custom_wall_collection):
            num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

        num_floor_meshes = 0
        if is_valid_ref(props.custom_floor_collection):
            num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

        num_roof_meshes = 0
        if is_valid_ref(props.custom_roof_collection):
            num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])

        mask_image = props.mask_image if is_valid_ref(props.mask_image) else None

        if props.floors > 1 and mask_image:
            mask_image = None
            self.report({'WARNING'}, "Mask image ignored when Floors > 1")

        effective_rings = props.polar_rings
        if props.grid_type == 'polar' and props.wall_mode == 'cube' and effective_rings % 2 == 1:
            effective_rings += 1
            self.report({'WARNING'}, f"Rings must be even when wall_mode='cube'; "
                                     f"increased from {props.polar_rings} to {effective_rings}")

        maze_data = generate_maze(
            width=props.width,
            depth=props.depth,
            seed=props.seed,
            mode=props.mode,
            emergency_exits=props.emergency_exits,
            algorithm=props.algorithm,
            rooms_enable=props.rooms_enable,
            rooms_count=props.rooms_count,
            min_room_size=props.min_room_size,
            max_room_size=props.max_room_size,
            loop_probability=props.loop_probability,
            isolated_wall_prob=props.isolated_wall_prob,
            entrance_side=props.entrance_side,
            exit_side=props.exit_side,
            num_entrances=props.num_entrances,
            num_exits=props.num_exits,
            num_wall_meshes=num_wall_meshes,
            num_floor_meshes=num_floor_meshes,
            num_roof_meshes=num_roof_meshes,
            wall_mode=props.wall_mode,
            mask_image=mask_image,
            mask_invert=props.mask_invert,
            grid_type=props.grid_type,
            polar_rings=effective_rings,
            floors=props.floors,
            stair_footprint=props.stair_footprint,
            stair_style=props.stair_style,
            stair_count=props.stair_count,
            stair_direction=props.stair_direction,
            selection_bias=props.selection_bias,
            straightness=props.straightness,
            direction_bias=props.direction_bias,
            east_bias=props.east_bias,
            orientation_bias=props.orientation_bias,
            passage_bias=props.passage_bias,
            eller_merge_prob=props.eller_merge_prob,
            radial_bias=props.radial_bias,
            maze_shape=props.maze_shape,
            shape_rotation=props.shape_rotation,
        )

        col = _find_or_create_maze_collection("FireMaze")
        props.fire_maze_collection_name = col.name
        
        col["fire_maze_data"] = json.dumps({
            'width': maze_data.width,
            'depth': maze_data.depth,
            'cells': maze_data.cells,
            'entrance': maze_data.entrance,
            'exits': maze_data.exits,
            'center': maze_data.center,
            'guide_path': maze_data.guide_path,
            'wall_mode': props.wall_mode,
            'num_wall_meshes': num_wall_meshes,
            'num_floor_meshes': num_floor_meshes,
            'num_roof_meshes': num_roof_meshes,
            'grid_type': maze_data.grid_type,
            'polar_rings': maze_data.polar_rings,
            'ring_sectors': maze_data.ring_sectors,
            'floors': maze_data.floors,
            'stairs': maze_data.stairs,
            'stair_count': props.stair_count,
            'maze_shape': props.maze_shape,
            'shape_rotation': props.shape_rotation,
            'smooth_shape_edges': props.smooth_shape_edges,
            'schema_version': 1,
        })


        props.edit_floor_level = props.floors - 1
        build_maze_objects(props, maze_data, context, collection=col)
        if props.generate_colliders:
            build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_Collider")

        save_autosave(context)

        if props.grid_type == 'rect':
            self.report({'INFO'}, f"Maze generated ({props.width}x{props.depth})")
        else:
            self.report({'INFO'}, f"Polar maze generated ({effective_rings} rings)")
        return {'FINISHED'}

class MAZE_OT_clear(bpy.types.Operator):
    """Delete all generated maze objects and collections from the scene."""

    bl_idname = "fire_maze.clear"
    bl_label = "Clear Maze"
    bl_description = "Delete all generated maze objects from the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """Remove all fire_maze objects, sweep orphaned datablocks, and clear the collection reference."""
        count = _remove_firemaze_objects(bpy.data.objects, sweep=True)

        _remove_maze_collections()

        context.scene.fire_maze.fire_maze_collection_name = ""
        self.report({'INFO'}, f"Removed {count} maze object(s)")
        return {'FINISHED'}
class MAZE_OT_interactive_edit(bpy.types.Operator):
    """Modal operator for interactive wall toggling and mesh cycling in the 3D viewport."""

    bl_idname = "fire_maze.interactive_edit"
    bl_label = "Interactive Maze Editor"
    bl_description = "Left-click to toggle walls. Shift+click to cycle custom mesh indices."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        """Return True when a rect or polar grid is selected."""
        props = context.scene.fire_maze
        return props.grid_type in {'rect', 'polar'}

    def _check_modal_early_exit_and_bounds(self, context, event, props):
        """Perform early checks for modal loop. Return tuple (action_taken, result_value)."""
        if context.area:
            context.area.tag_redraw()

        # Clean exit if toggled off by another button click
        if not props.is_editing:
            delete_edit_helper()
            context.workspace.status_text_set(None)
            set_other_mazes_visibility(context, True)
            col = _get_active_maze_collection(context)
            if col and getattr(self, "maze_data", None) is not None:
                if self._is_dirty:
                    col["fire_maze_data"] = json.dumps(self._maze_raw)
                    rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished")
            return True, {'FINISHED'}

        # Auto-exit interactive edit mode if the workspace/tab changed or active area is not 3D viewport
        if (getattr(self, "init_workspace", None) and context.workspace.name != self.init_workspace) or \
           context.area is None or context.area.type != 'VIEW_3D':
            context.workspace.status_text_set(None)
            props.is_editing = False
            delete_edit_helper()
            set_other_mazes_visibility(context, True)
            col = _get_active_maze_collection(context)
            if col and getattr(self, "maze_data", None) is not None:
                if self._is_dirty:
                    col["fire_maze_data"] = json.dumps(self._maze_raw)
                    rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished (auto-exited)")
            return True, {'FINISHED'}

        # Check if the mouse is within the main 3D viewport WINDOW region coordinates.
        if context.region:
            rx_min = context.region.x
            rx_max = rx_min + context.region.width
            ry_min = context.region.y
            ry_max = ry_min + context.region.height
            if not (rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max):
                return True, {'PASS_THROUGH'}

        # Check if the mouse is inside overlay regions within the viewport area
        if context.area:
            for region in context.area.regions:
                if region.type != 'WINDOW':
                    rx_min = region.x
                    rx_max = rx_min + region.width
                    ry_min = region.y
                    ry_max = ry_min + region.height
                    if rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max:
                        return True, {'PASS_THROUGH'}

        if event.type in {'RET', 'NUMPAD_ENTER', 'ESC'}:
            context.workspace.status_text_set(None)
            props.is_editing = False
            delete_edit_helper()
            set_other_mazes_visibility(context, True)
            col = _get_active_maze_collection(context)
            if col and getattr(self, "maze_data", None) is not None:
                if self._is_dirty:
                    col["fire_maze_data"] = json.dumps(self._maze_raw)
                    rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished")
            return True, {'FINISHED'}

        return False, None

    def _do_raycast(self, context, event):
        """Temporarily unhide helper, hide other maze objects, perform raycast, restore state."""
        helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
        hidden_objs = []
        try:
            if helper:
                helper.hide_viewport = False
            
            for col in bpy.data.collections:
                if "fire_maze_data" in col:
                    for o in col.objects:
                        if o.name != "_FireMaze_Edit_Helper" and not o.hide_viewport:
                            o.hide_viewport = True
                            hidden_objs.append(o)
            
            context.view_layer.update()
            obj, loc, normal = _raycast_from_mouse(context, event)
            return obj, loc, normal
        finally:
            if helper:
                helper.hide_viewport = True
            for o in hidden_objs:
                o.hide_viewport = False
            context.view_layer.update()

    def _handle_stair_tool(self, context, event, props, col, data_dict, z_hit, cx_clamped, cy_clamped, grid_type, original_cells, wall_mode):
        """Handle stair placement, rotation, and removal."""
        stairs = data_dict.get('stairs', [])
        existing_idx = -1
        for idx, s in enumerate(stairs):
            if s.get('z') == z_hit and s.get('x') == cx_clamped and s.get('y') == cy_clamped:
                existing_idx = idx
                break
        
        if existing_idx != -1:
            if event.shift:
                current_orient = stairs[existing_idx].get('orientation', 'N' if grid_type == 'rect' else 'IN')
                if grid_type == 'rect':
                    cycle_map = {'N': 'E', 'E': 'S', 'S': 'W', 'W': 'N'}
                    new_orient = cycle_map.get(current_orient, 'N')
                else:
                    cycle_map = {'CCW': 'OUT', 'OUT': 'CW', 'CW': 'IN', 'IN': 'CCW'}
                    new_orient = cycle_map.get(current_orient, 'IN')
                stairs[existing_idx]['orientation'] = new_orient
                self.report({'INFO'}, f"Rotated stair at cell ({cx_clamped}, {cy_clamped}) to {new_orient}")
            else:
                stairs.pop(existing_idx)
                self.report({'INFO'}, f"Removed stair at floor {z_hit}, cell ({cx_clamped}, {cy_clamped})")
        else:
            if z_hit < props.floors - 1:
                from .maze_algorithms.common_helpers import _force_cell_open
                _force_cell_open(original_cells, z_hit, cy_clamped, cx_clamped, wall_mode)
                _force_cell_open(original_cells, z_hit + 1, cy_clamped, cx_clamped, wall_mode)
                
                if grid_type == 'rect':
                    stair_orient = props.stair_direction
                else:
                    polar_orient_map = {'N': 'CCW', 'E': 'OUT', 'S': 'CW', 'W': 'IN'}
                    stair_orient = polar_orient_map.get(props.stair_direction, 'IN')
                
                new_stair = {
                    'z': z_hit,
                    'x': cx_clamped,
                    'y': cy_clamped,
                    'type': props.stair_style,
                    'footprint': '1x1' if grid_type == 'polar' else props.stair_footprint,
                    'orientation': stair_orient,
                }
                stairs.append(new_stair)
                self.report({'INFO'}, f"Placed stair at floor {z_hit}, cell ({cx_clamped}, {cy_clamped}) with orientation {stair_orient}")
            else:
                self.report({'WARNING'}, "Cannot place stair on the top floor")
                return None
        
        data_dict['stairs'] = stairs
        data_dict['cells'] = original_cells

        # Calculate dirty cells
        dirty_cells = set()
        if grid_type == 'polar':
            rings = data_dict.get('polar_rings', 5)
            ring_sectors = data_dict.get('ring_sectors')
            for z in [z_hit, z_hit + 1]:
                if 0 <= z < props.floors:
                    dirty_cells.add((z, cy_clamped, cx_clamped))
                    for dy in [-1, 0, 1]:
                        for dx in [-1, 0, 1]:
                            ny, nx = cy_clamped + dy, cx_clamped + dx
                            if 0 <= ny < rings:
                                Nr = ring_sectors[ny]
                                nx_wrapped = nx % Nr
                                dirty_cells.add((z, ny, nx_wrapped))
        else:
            width = data_dict['width']
            depth = data_dict['depth']
            for z in [z_hit, z_hit + 1]:
                if 0 <= z < props.floors:
                    dirty_cells.add((z, cy_clamped, cx_clamped))
                    for dy in [-1, 0, 1]:
                        for dx in [-1, 0, 1]:
                            ny, nx = cy_clamped + dy, cx_clamped + dx
                            if 0 <= ny < depth and 0 <= nx < width:
                                dirty_cells.add((z, ny, nx))
        return dirty_cells

    def _handle_polar_mesh_cycle(self, context, event, props, col, data_dict, cells, wall_mode, ring_sectors, rings, hit_x, hit_y, ts, r_hit, phi_hit, r_idx, alpha_r, theta, Nr, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit):
        """Cycle wall/floor/roof mesh index on a polar cell under Shift+click."""
        modified = False
        rebuilt_text = ""
        tr, tt = r_idx, theta
        is_wall = cells[r_idx][theta][0] if wall_mode == 'cube' else False
        
        if face_dir == 'ROOF':
            if not (wall_mode == 'cube' and not is_wall):
                roof_idx_pos = 5
                if num_roof_meshes > 0 and len(cells[r_idx][theta]) > roof_idx_pos:
                    current_idx = cells[r_idx][theta][roof_idx_pos] if isinstance(cells[r_idx][theta][roof_idx_pos], int) else -1
                    cells[r_idx][theta][roof_idx_pos] = (current_idx + 1) % num_roof_meshes
                    modified = True
                    rebuilt_text = "roof"
        elif face_dir == 'FLOOR':
            if not (wall_mode == 'cube' and is_wall):
                floor_idx_pos = 4
                if num_floor_meshes > 0 and len(cells[r_idx][theta]) > floor_idx_pos:
                    current_idx = cells[r_idx][theta][floor_idx_pos] if isinstance(cells[r_idx][theta][floor_idx_pos], int) else -1
                    cells[r_idx][theta][floor_idx_pos] = (current_idx + 1) % num_floor_meshes
                    modified = True
                    rebuilt_text = "floor"
        else:
            # Clicked a wall face
            if wall_mode == 'cube':
                if num_wall_meshes > 0:
                    if props.cube_mode_pillar:
                        current_idx = cells[r_idx][theta][2] if isinstance(cells[r_idx][theta][2], int) else -1
                        cells[r_idx][theta][2] = (current_idx + 1) % num_wall_meshes
                        modified = True
                        rebuilt_text = "pillar"
                    else:
                        d_in = abs(r_hit - (r_idx - 0.5) * ts)
                        d_out = abs(r_hit - (r_idx + 0.5) * ts)
                        
                        d_cw = r_hit * _angle_diff(phi_hit, theta * alpha_r)
                        d_ccw = r_hit * _angle_diff(phi_hit, (theta + 1) * alpha_r)

                        if r_idx == 0:
                            min_d = d_out
                        else:
                            min_d = min(d_in, d_out, d_cw, d_ccw)
                            
                        owner_cell = None
                        owner_idx_pos = -1
                        boundary_name = ""
                        
                        if min_d == d_cw:
                            A = (r_idx, theta)
                            B = (r_idx, (theta - 1) % Nr)
                            if not cells[A[0]][A[1]][0]:
                                owner_cell = A
                                owner_idx_pos = 3
                                boundary_name = "clockwise radial boundary wall"
                            elif not cells[B[0]][B[1]][0]:
                                owner_cell = B
                                owner_idx_pos = 2
                                boundary_name = "counter-clockwise radial boundary wall"
                            else:
                                owner_cell = A
                                owner_idx_pos = 3
                                boundary_name = "clockwise radial boundary wall (fallback)"
                        elif min_d == d_ccw:
                            A = (r_idx, theta)
                            B = (r_idx, (theta + 1) % Nr)
                            if not cells[A[0]][A[1]][0]:
                                owner_cell = A
                                owner_idx_pos = 2
                                boundary_name = "counter-clockwise radial boundary wall"
                            elif not cells[B[0]][B[1]][0]:
                                owner_cell = B
                                owner_idx_pos = 3
                                boundary_name = "clockwise radial boundary wall"
                            else:
                                owner_cell = A
                                owner_idx_pos = 2
                                boundary_name = "counter-clockwise radial boundary wall (fallback)"
                        elif min_d == d_in:
                            if r_idx > 0:
                                N_in = ring_sectors[r_idx - 1]
                                theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                                A = (r_idx, theta)
                                B = (r_idx - 1, theta_in)
                                if not cells[A[0]][A[1]][0]:
                                    owner_cell = A
                                    owner_idx_pos = 7
                                    boundary_name = "inward angular boundary wall"
                                elif not cells[B[0]][B[1]][0]:
                                    owner_cell = B
                                    owner_idx_pos = 8
                                    boundary_name = "outward angular boundary wall"
                                else:
                                    owner_cell = A
                                    owner_idx_pos = 7
                                    boundary_name = "inward angular boundary wall (fallback)"
                        else: # d_out
                            if r_idx < rings - 1:
                                N_out = ring_sectors[r_idx + 1]
                                theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                                theta_out = max(0, min(theta_out, N_out - 1))
                                A = (r_idx, theta)
                                B = (r_idx + 1, theta_out)
                                if not cells[A[0]][A[1]][0]:
                                    owner_cell = A
                                    owner_idx_pos = 8
                                    boundary_name = "outward angular boundary wall"
                                elif not cells[B[0]][B[1]][0]:
                                    owner_cell = B
                                    owner_idx_pos = 7
                                    boundary_name = "inward angular boundary wall"
                                else:
                                    owner_cell = A
                                    owner_idx_pos = 8
                                    boundary_name = "outward angular boundary wall (fallback)"
                            else:
                                owner_cell = (r_idx, theta)
                                owner_idx_pos = 6
                                boundary_name = "outermost outward angular boundary wall"
                        
                        if owner_cell is not None:
                            r_own, theta_own = owner_cell
                            if len(cells[r_own][theta_own]) < 8:
                                if owner_idx_pos in {2, 3}:
                                    owner_idx_pos = 2
                                elif owner_idx_pos in {6, 7, 8}:
                                    owner_idx_pos = 3
                            
                            current_idx = cells[r_own][theta_own][owner_idx_pos] if isinstance(cells[r_own][theta_own][owner_idx_pos], int) else -1
                            cells[r_own][theta_own][owner_idx_pos] = (current_idx + 1) % num_wall_meshes
                            modified = True
                            rebuilt_text = boundary_name
                            r_idx, theta = r_own, theta_own
                            tr, tt = r_own, theta_own
            else:
                # Thin wall mode
                d_in = abs(r_hit - (r_idx - 0.5) * ts)
                d_out = abs(r_hit - (r_idx + 0.5) * ts)
                
                d_cw = r_hit * _angle_diff(phi_hit, theta * alpha_r)
                d_ccw = r_hit * _angle_diff(phi_hit, (theta + 1) * alpha_r)
                
                if r_idx == 0:
                    min_d = d_out
                else:
                    min_d = min(d_in, d_out, d_cw, d_ccw)
                    
                if min_d == d_cw:
                    if num_wall_meshes > 0:
                        tr, tt = r_idx, theta
                        current_idx = cells[r_idx][theta][2] if isinstance(cells[r_idx][theta][2], int) else -1
                        cells[r_idx][theta][2] = (current_idx + 1) % num_wall_meshes
                        modified = True
                        rebuilt_text = "clockwise wall"
                elif min_d == d_ccw:
                    if num_wall_meshes > 0:
                        next_theta = (theta + 1) % Nr
                        tr, tt = r_idx, next_theta
                        current_idx = cells[r_idx][next_theta][2] if isinstance(cells[r_idx][next_theta][2], int) else -1
                        cells[r_idx][next_theta][2] = (current_idx + 1) % num_wall_meshes
                        modified = True
                        rebuilt_text = "counter-clockwise wall"
                elif min_d == d_in:
                    if num_wall_meshes > 0:
                        tr, tt = r_idx, theta
                        current_idx = cells[r_idx][theta][3] if isinstance(cells[r_idx][theta][3], int) else -1
                        cells[r_idx][theta][3] = (current_idx + 1) % num_wall_meshes
                        modified = True
                        rebuilt_text = "inward wall"
                elif min_d == d_out:
                    if num_wall_meshes > 0:
                        if r_idx + 1 < rings:
                            N_out = ring_sectors[r_idx + 1]
                            theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                            theta_out = max(0, min(theta_out, N_out - 1))
                            tr, tt = r_idx + 1, theta_out
                            current_idx = cells[r_idx + 1][theta_out][3] if isinstance(cells[r_idx + 1][theta_out][3], int) else -1
                            cells[r_idx + 1][theta_out][3] = (current_idx + 1) % num_wall_meshes
                            modified = True
                            rebuilt_text = "outward wall"
                        else:
                            tr, tt = r_idx, theta
                            current_idx = cells[r_idx][theta][6] if len(cells[r_idx][theta]) > 6 else (cells[r_idx][theta][3] if isinstance(cells[r_idx][theta][3], int) else -1)
                            if len(cells[r_idx][theta]) > 6:
                                cells[r_idx][theta][6] = (current_idx + 1) % num_wall_meshes
                            else:
                                cells[r_idx][theta][3] = (current_idx + 1) % num_wall_meshes
                            modified = True
                            rebuilt_text = "outermost outward wall"
                    
        if modified:
            data_dict['cells'] = original_cells
            self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at cell ({tr}, {tt})")
            
            # Calculate dirty cells using overlapping sector check
            Nr_tr = ring_sectors[tr]
            dirty_cells = {(z_hit, tr, tt)}
            dirty_cells.add((z_hit, tr, (tt - 1) % Nr_tr))
            dirty_cells.add((z_hit, tr, (tt + 1) % Nr_tr))
            
            alpha_tr = 2 * math.pi / Nr_tr
            A = tt * alpha_tr
            B = (tt + 1) * alpha_tr
            
            if tr > 0:
                N_in = ring_sectors[tr - 1]
                alpha_in = 2 * math.pi / N_in
                for t_in in range(N_in):
                    A_in = t_in * alpha_in
                    B_in = (t_in + 1) * alpha_in
                    if max(A, A_in) < min(B, B_in) + 1e-5:
                        dirty_cells.add((z_hit, tr - 1, t_in))
            if tr < rings - 1:
                N_out = ring_sectors[tr + 1]
                alpha_out = 2 * math.pi / N_out
                for t_out in range(N_out):
                    A_out = t_out * alpha_out
                    B_out = (t_out + 1) * alpha_out
                    if max(A, A_out) < min(B, B_out) + 1e-5:
                        dirty_cells.add((z_hit, tr + 1, t_out))
            return dirty_cells
        return None

    def _handle_polar_wall_toggle(self, context, event, props, col, data_dict, cells, wall_mode, ring_sectors, rings, hit_x, hit_y, ts, r_hit, phi_hit, r_idx, alpha_r, theta, Nr, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit):
        """Toggle a polar wall cell between wall and floor, handling entrance/exit moves.

        Called for ALL polar grid edits — wall_mode comes from the stored
        generation-time setting (data_dict), not from grid_type, so polar
        mazes can reach either branch:
          - wall_mode == 'cube': toggle entire wedge cells (cells[r][t][0])
          - wall_mode != 'cube': per-edge toggling (CW/CCW/IN/OUT) plus
            entrance/exit editing on the outermost ring
        """
        modified = False
        rebuilt_text = ""
        tr, tt = r_idx, theta
        
        if wall_mode == 'cube':
            target_cell = (r_idx, theta)
            if face_dir == 'WALL':
                d_in = abs(r_hit - (r_idx - 0.5) * ts)
                d_out = abs(r_hit - (r_idx + 0.5) * ts)
                
                d_cw = r_hit * _angle_diff(phi_hit, theta * alpha_r)
                d_ccw = r_hit * _angle_diff(phi_hit, (theta + 1) * alpha_r)

                if r_idx == 0:
                    min_d = d_out
                else:
                    min_d = min(d_in, d_out, d_cw, d_ccw)
                    
                if min_d == d_cw:
                    A = (r_idx, theta)
                    B = (r_idx, (theta - 1) % Nr)
                    if cells[A[0]][A[1]][0]:
                        target_cell = A
                    elif cells[B[0]][B[1]][0]:
                        target_cell = B
                elif min_d == d_ccw:
                    A = (r_idx, theta)
                    B = (r_idx, (theta + 1) % Nr)
                    if cells[A[0]][A[1]][0]:
                        target_cell = A
                    elif cells[B[0]][B[1]][0]:
                        target_cell = B
                elif min_d == d_in:
                    if r_idx > 0:
                        N_in = ring_sectors[r_idx - 1]
                        theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                        A = (r_idx, theta)
                        B = (r_idx - 1, theta_in)
                        if cells[A[0]][A[1]][0]:
                            target_cell = A
                        elif cells[B[0]][B[1]][0]:
                            target_cell = B
                else: # d_out
                    if r_idx < rings - 1:
                        N_out = ring_sectors[r_idx + 1]
                        theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                        theta_out = max(0, min(theta_out, N_out - 1))
                        A = (r_idx, theta)
                        B = (r_idx + 1, theta_out)
                        if cells[A[0]][A[1]][0]:
                            target_cell = A
                        elif cells[B[0]][B[1]][0]:
                            target_cell = B
                        
            tr, tt = target_cell
            is_perimeter = (tr == rings - 1)
            was_wall = cells[tr][tt][0]
            floors = data_dict.get('floors', props.floors)
            
            rng = get_rng()
            cells[tr][tt][0] = not was_wall
            if cells[tr][tt][0]:
                if num_wall_meshes > 0:
                    cells[tr][tt][2] = 0
                    cells[tr][tt][3] = 0
                    if len(cells[tr][tt]) > 7:
                        cells[tr][tt][7] = 0
                    if len(cells[tr][tt]) > 8:
                        cells[tr][tt][8] = 0
                if num_roof_meshes > 0 and len(cells[tr][tt]) > 5:
                    cells[tr][tt][5] = rng.randrange(num_roof_meshes)
            else:
                if num_floor_meshes > 0 and len(cells[tr][tt]) > 4:
                    cells[tr][tt][IDX_FLOOR] = rng.randrange(num_floor_meshes)
            
            if is_perimeter:
                if was_wall: # Now floor cell (was wall)
                    if z_hit == 0:
                        # Move entrance here
                        # Close the old entrance cell if one exists
                        entrance_val = data_dict.get('entrance')
                        if entrance_val and len(entrance_val) >= 2:
                            old_r, old_tt = entrance_val[0], entrance_val[1]
                            if 0 <= old_r < len(cells) and 0 <= old_tt < len(cells[old_r]):
                                cells[old_r][old_tt][0] = True # Make it wall again
                                self._old_entrance_dirty = (old_r, old_tt)
                        
                        data_dict['entrance'] = [tr, tt, 'OUT']
                        rebuilt_text = "entrance floor tile (moved)"
                    elif z_hit == floors - 1:
                        # Add to exits
                        if 'exits' not in data_dict or data_dict['exits'] is None:
                            data_dict['exits'] = []
                        if not any(ex[0] == tr and ex[1] == tt and ex[2] == 'OUT' for ex in data_dict['exits']):
                            data_dict['exits'].append([tr, tt, 'OUT'])
                        rebuilt_text = "exit floor tile"
                    else:
                        rebuilt_text = "floor tile (middle floor)"
                else: # Now wall cell (was floor)
                    if z_hit == 0:
                        # Close entrance
                        entrance_val = data_dict.get('entrance')
                        if entrance_val and len(entrance_val) >= 2 and entrance_val[0] == tr and entrance_val[1] == tt:
                            data_dict['entrance'] = None
                        rebuilt_text = "entrance wall tile (closed)"
                    elif z_hit == floors - 1:
                        # Remove from exits
                        if 'exits' in data_dict and data_dict['exits'] is not None:
                            data_dict['exits'] = [ex for ex in data_dict['exits'] if not (ex[0] == tr and ex[1] == tt and ex[2] == 'OUT')]
                        rebuilt_text = "exit wall tile (closed)"
                    else:
                        rebuilt_text = "wall tile (middle floor)"
            else:
                rebuilt_text = "wall tile"

            modified = True
            r_idx, theta = tr, tt
        else:
            # Thin-mode polar wall toggle — reachable when a polar maze was
            # generated with wall_mode='thin'. Detects the clicked edge
            # and toggles CW/CCW/IN/OUT walls. The outermost-ring d_out
            # case also handles entrance/exit placement.
            d_in = abs(r_hit - (r_idx - 0.5) * ts)
            d_out = abs(r_hit - (r_idx + 0.5) * ts)
            
            d_cw = r_hit * _angle_diff(phi_hit, theta * alpha_r)
            d_ccw = r_hit * _angle_diff(phi_hit, (theta + 1) * alpha_r)
            
            if r_idx == 0:
                min_d = d_out
            else:
                min_d = min(d_in, d_out, d_cw, d_ccw)
                
            if min_d == d_cw:
                tr, tt = r_idx, theta
                cells[r_idx][theta][0] = not cells[r_idx][theta][0]
                modified = True
                rebuilt_text = "clockwise wall"
            elif min_d == d_ccw:
                next_theta = (theta + 1) % Nr
                tr, tt = r_idx, next_theta
                cells[r_idx][next_theta][0] = not cells[r_idx][next_theta][0]
                modified = True
                rebuilt_text = "counter-clockwise wall"
            elif min_d == d_in:
                tr, tt = r_idx, theta
                cells[r_idx][theta][1] = not cells[r_idx][theta][1]
                modified = True
                rebuilt_text = "inward wall"
            elif min_d == d_out:
                if r_idx + 1 < rings:
                    N_out = ring_sectors[r_idx + 1]
                    theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                    theta_out = max(0, min(theta_out, N_out - 1))
                    tr, tt = r_idx + 1, theta_out
                    cells[r_idx + 1][theta_out][1] = not cells[r_idx + 1][theta_out][1]
                    modified = True
                    rebuilt_text = "outward wall"
                else:
                    theta_out = theta
                    tr, tt = r_idx, theta_out
                    floors = data_dict.get('floors', props.floors)
                    if z_hit == 0:
                        # Floor 0: Edit entrance
                        entrance_val = data_dict.get('entrance')
                        is_current_entrance = False
                        if entrance_val and len(entrance_val) >= 2 and entrance_val[0] == r_idx and entrance_val[1] == theta_out:
                            is_current_entrance = True
                        
                        if is_current_entrance:
                            self._old_entrance_dirty = (r_idx, theta_out)
                            data_dict['entrance'] = None
                            rebuilt_text = "entrance wall (closed)"
                            modified = True
                        else:
                            if entrance_val and len(entrance_val) >= 2:
                                old_r, old_tt = entrance_val[0], entrance_val[1]
                                self._old_entrance_dirty = (old_r, old_tt)
                            data_dict['entrance'] = [r_idx, theta_out, 'OUT']
                            rebuilt_text = "entrance wall (moved)"
                            modified = True
                    elif z_hit == floors - 1:
                        # Top floor: Edit exits
                        if 'exits' not in data_dict or data_dict['exits'] is None:
                            data_dict['exits'] = []
                        found_idx = -1
                        for idx, ex in enumerate(data_dict['exits']):
                            if len(ex) >= 3 and ex[0] == r_idx and ex[1] == theta_out and ex[2] == 'OUT':
                                found_idx = idx
                                break
                        if found_idx != -1:
                            data_dict['exits'].pop(found_idx)
                            rebuilt_text = "outward wall (exit closed)"
                        else:
                            data_dict['exits'].append([r_idx, theta_out, 'OUT'])
                            rebuilt_text = "outward wall (exit opened)"
                        modified = True
                    else:
                        self.report({'WARNING'}, "Cannot place entrance/exit on middle floors")
                        modified = False

        if modified:
            data_dict['cells'] = original_cells
            self.report({'INFO'}, f"Toggled {rebuilt_text} at cell ({tr}, {tt})")
            
            # Calculate dirty cells using overlapping sector check
            dirty_cells = set()
            
            def add_overlapping_dirty(z, r, theta, dirty_set):
                """Add (z, r, theta) and all overlapping neighbours in adjacent rings to dirty_set."""
                Nr = ring_sectors[r]
                dirty_set.add((z, r, theta))
                dirty_set.add((z, r, (theta - 1) % Nr))
                dirty_set.add((z, r, (theta + 1) % Nr))
                
                alpha = 2 * math.pi / Nr
                A = theta * alpha
                B = (theta + 1) * alpha
                
                if r > 0:
                    N_in = ring_sectors[r - 1]
                    alpha_in = 2 * math.pi / N_in
                    for t_in in range(N_in):
                        A_in = t_in * alpha_in
                        B_in = (t_in + 1) * alpha_in
                        if max(A, A_in) < min(B, B_in) + 1e-5:
                            dirty_set.add((z, r - 1, t_in))
                if r < rings - 1:
                    N_out = ring_sectors[r + 1]
                    alpha_out = 2 * math.pi / N_out
                    for t_out in range(N_out):
                        A_out = t_out * alpha_out
                        B_out = (t_out + 1) * alpha_out
                        if max(A, A_out) < min(B, B_out) + 1e-5:
                            dirty_set.add((z, r + 1, t_out))

            add_overlapping_dirty(z_hit, tr, tt, dirty_cells)
            if getattr(self, "_old_entrance_dirty", None) is not None:
                old_r, old_tt = self._old_entrance_dirty
                add_overlapping_dirty(z_hit, old_r, old_tt, dirty_cells)
                self._old_entrance_dirty = None
            
            return dirty_cells
        return None

    def _handle_rect_mesh_cycle(self, context, event, props, col, data_dict, cells, wall_mode, hit_x, hit_y, ts, width, depth, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit):
        """Cycle wall/floor/roof mesh index on a rectangular cell under Shift+click."""
        cx = math.floor(hit_x / ts)
        cy = math.floor(hit_y / ts)
        if -1 <= cx <= width and -1 <= cy <= depth:
            cx_clamped = max(0, min(cx, width - 1))
            cy_clamped = max(0, min(cy, depth - 1))

            if wall_mode == 'cube':
                is_wall = cells[cy_clamped][cx_clamped][0]
                modified = False
                rebuilt_text = ""
                
                is_pillar_mode = props.cube_mode_pillar
                
                if face_dir == 'ROOF':
                    if is_pillar_mode and is_wall:
                        if num_wall_meshes > 0:
                            current_idx = cells[cy_clamped][cx_clamped][1] if isinstance(cells[cy_clamped][cx_clamped][1], int) else -1
                            next_idx = (current_idx + 1) % num_wall_meshes
                            cells[cy_clamped][cx_clamped][1] = next_idx
                            cells[cy_clamped][cx_clamped][2] = next_idx
                            cells[cy_clamped][cx_clamped][3] = next_idx
                            cells[cy_clamped][cx_clamped][4] = next_idx
                            modified = True
                            rebuilt_text = "pillar"
                    else:
                        if is_wall and num_roof_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 6:
                            current_idx = cells[cy_clamped][cx_clamped][6] if isinstance(cells[cy_clamped][cx_clamped][6], int) else -1
                            cells[cy_clamped][cx_clamped][6] = (current_idx + 1) % num_roof_meshes
                            modified = True
                            rebuilt_text = "roof"
                elif face_dir == 'FLOOR':
                    if not is_wall and num_floor_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 5:
                        current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                        cells[cy_clamped][cx_clamped][5] = (current_idx + 1) % num_floor_meshes
                        modified = True
                        rebuilt_text = "floor"
                else:
                    # Clicked a wall face
                    if is_wall and num_wall_meshes > 0:
                        if is_pillar_mode:
                            current_idx = cells[cy_clamped][cx_clamped][1] if isinstance(cells[cy_clamped][cx_clamped][1], int) else -1
                            next_idx = (current_idx + 1) % num_wall_meshes
                            cells[cy_clamped][cx_clamped][1] = next_idx
                            cells[cy_clamped][cx_clamped][2] = next_idx
                            cells[cy_clamped][cx_clamped][3] = next_idx
                            cells[cy_clamped][cx_clamped][4] = next_idx
                            modified = True
                            rebuilt_text = "pillar"
                        else:
                            actual_face = face_dir
                            if actual_face not in {'N', 'S', 'E', 'W'}:
                                d_N = abs(hit_y - (cy_clamped + 1) * ts)
                                d_S = abs(hit_y - cy_clamped * ts)
                                d_E = abs(hit_x - (cx_clamped + 1) * ts)
                                d_W = abs(hit_x - cx_clamped * ts)
                                min_d = min(d_N, d_S, d_E, d_W)
                                if min_d == d_N:
                                    actual_face = 'N'
                                elif min_d == d_S:
                                    actual_face = 'S'
                                elif min_d == d_E:
                                    actual_face = 'E'
                                else:
                                    actual_face = 'W'
                            idx_map = {'N': 1, 'S': 2, 'E': 3, 'W': 4}
                            face_idx = idx_map.get(actual_face, 1)
                            current_idx = cells[cy_clamped][cx_clamped][face_idx] if isinstance(cells[cy_clamped][cx_clamped][face_idx], int) else -1
                            cells[cy_clamped][cx_clamped][face_idx] = (current_idx + 1) % num_wall_meshes
                            modified = True
                            rebuilt_text = f"wall ({actual_face} face)"
                            
                if modified:
                    data_dict['cells'] = original_cells
                    self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at ({cx_clamped}, {cy_clamped})")
                else:
                    # dirty_cells for this branch is built by the outer
                    # `if modified:` block at ~1435 using (cx_clamped, cy_clamped)
                    if not is_wall:
                        if num_floor_meshes > 0:
                            current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                            cells[cy_clamped][cx_clamped][5] = (current_idx + 1) % num_floor_meshes
                            data_dict['cells'] = original_cells
                            modified = True
                            self.report({'INFO'}, f"Swapped floor mesh at ({cx_clamped}, {cy_clamped})")
            else:
                # Thin wall mode
                modified = False
                rebuilt_text = ""
                if face_dir == 'ROOF':
                    if num_roof_meshes > 0:
                        if len(cells[cy_clamped][cx_clamped]) > 8:
                            idx_pos = 9
                        elif len(cells[cy_clamped][cx_clamped]) > 7:
                            idx_pos = 7
                        else:
                            idx_pos = None
                        
                        if idx_pos is not None:
                            current_idx = cells[cy_clamped][cx_clamped][idx_pos] if isinstance(cells[cy_clamped][cx_clamped][idx_pos], int) else -1
                            cells[cy_clamped][cx_clamped][idx_pos] = (current_idx + 1) % num_roof_meshes
                            modified = True
                            rebuilt_text = "roof"
                elif face_dir == 'FLOOR':
                    if num_floor_meshes > 0:
                        if len(cells[cy_clamped][cx_clamped]) > 8:
                            idx_pos = 8
                        elif len(cells[cy_clamped][cx_clamped]) > 6:
                            idx_pos = 6
                        else:
                            idx_pos = None
                            
                        if idx_pos is not None:
                            current_idx = cells[cy_clamped][cx_clamped][idx_pos] if isinstance(cells[cy_clamped][cx_clamped][idx_pos], int) else -1
                            cells[cy_clamped][cx_clamped][idx_pos] = (current_idx + 1) % num_floor_meshes
                            modified = True
                            rebuilt_text = "floor"
                else:
                    if num_wall_meshes > 0:
                        d_N = abs(hit_y - (cy_clamped + 1) * ts)
                        d_S = abs(hit_y - cy_clamped * ts)
                        d_E = abs(hit_x - (cx_clamped + 1) * ts)
                        d_W = abs(hit_x - cx_clamped * ts)
                        
                        min_d = min(d_N, d_S, d_E, d_W)
                        
                        if len(cells[cy_clamped][cx_clamped]) > 8:
                            if min_d == d_N:
                                 current_idx = cells[cy_clamped][cx_clamped][4] if isinstance(cells[cy_clamped][cx_clamped][4], int) else -1
                                 next_idx = (current_idx + 1) % num_wall_meshes
                                 cells[cy_clamped][cx_clamped][4] = next_idx
                                 if cy_clamped + 1 < depth:
                                     cells[cy_clamped + 1][cx_clamped][5] = next_idx
                                 modified = True
                                 rebuilt_text = "horizontal wall (North)"
                            elif min_d == d_S:
                                 current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                                 next_idx = (current_idx + 1) % num_wall_meshes
                                 cells[cy_clamped][cx_clamped][5] = next_idx
                                 if cy_clamped - 1 >= 0:
                                     cells[cy_clamped - 1][cx_clamped][4] = next_idx
                                 modified = True
                                 rebuilt_text = "horizontal wall (South)"
                            elif min_d == d_E:
                                 current_idx = cells[cy_clamped][cx_clamped][6] if isinstance(cells[cy_clamped][cx_clamped][6], int) else -1
                                 next_idx = (current_idx + 1) % num_wall_meshes
                                 cells[cy_clamped][cx_clamped][6] = next_idx
                                 if cx_clamped + 1 < width:
                                     cells[cy_clamped][cx_clamped + 1][7] = next_idx
                                 modified = True
                                 rebuilt_text = "vertical wall (East)"
                            else:
                                 current_idx = cells[cy_clamped][cx_clamped][7] if isinstance(cells[cy_clamped][cx_clamped][7], int) else -1
                                 next_idx = (current_idx + 1) % num_wall_meshes
                                 cells[cy_clamped][cx_clamped][7] = next_idx
                                 if cx_clamped - 1 >= 0:
                                     cells[cy_clamped][cx_clamped - 1][6] = next_idx
                                 modified = True
                                 rebuilt_text = "vertical wall (West)"
                        else:
                            if min_d == d_N or min_d == d_S:
                                target_y = cy_clamped + 1 if min_d == d_N else cy_clamped
                                target_y = min(target_y, depth - 1)
                                current_idx = cells[target_y][cx_clamped][4] if isinstance(cells[target_y][cx_clamped][4], int) else -1
                                cells[target_y][cx_clamped][4] = (current_idx + 1) % num_wall_meshes
                                cy_clamped = target_y
                                modified = True
                                rebuilt_text = "horizontal wall"
                            else:
                                target_x = cx_clamped + 1 if min_d == d_E else cx_clamped
                                target_x = min(target_x, width - 1)
                                current_idx = cells[cy_clamped][target_x][5] if isinstance(cells[cy_clamped][target_x][5], int) else -1
                                cells[cy_clamped][target_x][5] = (current_idx + 1) % num_wall_meshes
                                cx_clamped = target_x
                                modified = True
                                rebuilt_text = "vertical wall"
                     
            if modified:
                # Calculate dirty cells
                dirty_cells = set()
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        ny, nx = cy_clamped + dy, cx_clamped + dx
                        if 0 <= ny < depth and 0 <= nx < width:
                            dirty_cells.add((z_hit, ny, nx))
                return dirty_cells
        return None

    def _handle_rect_wall_toggle(self, context, event, props, col, data_dict, cells, wall_mode, hit_x, hit_y, ts, width, depth, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit):
        """Toggle a rectangular wall cell between wall and floor, handling entrance/exit moves."""
        modified = False
        if wall_mode == 'cube':
            cx = math.floor(hit_x / ts)
            cy = math.floor(hit_y / ts)
            cx_clamped = max(0, min(cx, width - 1))
            cy_clamped = max(0, min(cy, depth - 1))
            
            target_cell = (cx_clamped, cy_clamped)
            if face_dir in {'N', 'S', 'E', 'W'}:
                A = (cx_clamped, cy_clamped)
                if face_dir == 'N':
                    B = (cx_clamped, cy_clamped + 1)
                elif face_dir == 'S':
                    B = (cx_clamped, cy_clamped - 1)
                elif face_dir == 'E':
                    B = (cx_clamped + 1, cy_clamped)
                else:
                    B = (cx_clamped - 1, cy_clamped)
                    
                if 0 <= B[0] < width and 0 <= B[1] < depth:
                    if cells[A[1]][A[0]][0]:
                        target_cell = A
                    elif cells[B[1]][B[0]][0]:
                        target_cell = B
                        
            tx, ty = target_cell
            if 0 <= tx < width and 0 <= ty < depth:
                was_wall = cells[ty][tx][0]
                floors = data_dict.get('floors', props.floors)
                rng = get_rng()
                cells[ty][tx][0] = not was_wall
                if cells[ty][tx][0]:
                    if num_wall_meshes > 0:
                        cells[ty][tx][1] = 0
                        cells[ty][tx][2] = 0
                        cells[ty][tx][3] = 0
                        cells[ty][tx][4] = 0
                    if num_roof_meshes > 0:
                        cells[ty][tx][6] = rng.randrange(num_roof_meshes)
                else:
                    if num_floor_meshes > 0:
                        cells[ty][tx][5] = rng.randrange(num_floor_meshes)
                
                is_perimeter = (tx == 0 or tx == width - 1 or ty == 0 or ty == depth - 1)
                if is_perimeter:
                    if was_wall:  # Toggled from wall to floor -> Move entrance/exit here
                        if ty == depth - 1:
                            d = 'N'
                        elif ty == 0:
                            d = 'S'
                        elif tx == width - 1:
                            d = 'E'
                        else:
                            d = 'W'
                            
                        if z_hit == 0:
                            # Move entrance here
                            entrance_val = data_dict.get('entrance')
                            if entrance_val and len(entrance_val) >= 2:
                                old_x, old_y = entrance_val[0], entrance_val[1]
                                if 0 <= old_x < width and 0 <= old_y < depth:
                                    cells[old_y][old_x][0] = True  # Make it wall again
                                    self._old_entrance_dirty = (old_x, old_y)
                            data_dict['entrance'] = [tx, ty, d]
                            rebuilt_text = "entrance floor tile (moved)"
                        elif z_hit == floors - 1:
                            # Add to exits
                            if 'exits' not in data_dict or data_dict['exits'] is None:
                                data_dict['exits'] = []
                            if not any(ex[0] == tx and ex[1] == ty for ex in data_dict['exits']):
                                data_dict['exits'].append([tx, ty, d])
                            rebuilt_text = "exit floor tile"
                        else:
                            rebuilt_text = "floor tile (middle floor)"
                    else:  # Toggled from floor to wall -> Close/remove entrance/exit
                        if z_hit == 0:
                            entrance_val = data_dict.get('entrance')
                            if entrance_val and len(entrance_val) >= 2 and entrance_val[0] == tx and entrance_val[1] == ty:
                                data_dict['entrance'] = None
                            rebuilt_text = "entrance wall tile (closed)"
                        elif z_hit == floors - 1:
                            if 'exits' in data_dict and data_dict['exits'] is not None:
                                data_dict['exits'] = [ex for ex in data_dict['exits'] if not (ex[0] == tx and ex[1] == ty)]
                            rebuilt_text = "exit wall tile (closed)"
                        else:
                            rebuilt_text = "wall tile (middle floor)"
                else:
                    rebuilt_text = "wall tile" if cells[ty][tx][0] else "floor tile"
                
                data_dict['cells'] = original_cells
                self.report({'INFO'}, f"Toggled {rebuilt_text} at ({tx}, {ty})")
                modified = True
                cx_clamped, cy_clamped = tx, ty
        else:
            # Thin wall mode
            cx = math.floor(hit_x / ts)
            cy = math.floor(hit_y / ts)
            if -1 <= cx <= width and -1 <= cy <= depth:
                cx_clamped = max(0, min(cx, width - 1))
                cy_clamped = max(0, min(cy, depth - 1))
                
                d_N = abs(hit_y - (cy_clamped + 1) * ts)
                d_S = abs(hit_y - cy_clamped * ts)
                d_E = abs(hit_x - (cx_clamped + 1) * ts)
                d_W = abs(hit_x - cx_clamped * ts)
                
                min_d = min(d_N, d_S, d_E, d_W)
                
                if min_d == d_N:
                    cells[cy_clamped][cx_clamped][0] = not cells[cy_clamped][cx_clamped][0]
                    if cy_clamped + 1 < depth:
                        cells[cy_clamped + 1][cx_clamped][1] = cells[cy_clamped][cx_clamped][0]
                elif min_d == d_S:
                    cells[cy_clamped][cx_clamped][1] = not cells[cy_clamped][cx_clamped][1]
                    if cy_clamped - 1 >= 0:
                        cells[cy_clamped - 1][cx_clamped][0] = cells[cy_clamped][cx_clamped][1]
                elif min_d == d_E:
                    cells[cy_clamped][cx_clamped][2] = not cells[cy_clamped][cx_clamped][2]
                    if cx_clamped + 1 < width:
                        cells[cy_clamped][cx_clamped + 1][3] = cells[cy_clamped][cx_clamped][2]
                else:
                    cells[cy_clamped][cx_clamped][3] = not cells[cy_clamped][cx_clamped][3]
                    if cx_clamped - 1 >= 0:
                        cells[cy_clamped][cx_clamped - 1][2] = cells[cy_clamped][cx_clamped][3]
                        
                toggled_border = None
                if min_d == d_N and cy_clamped == depth - 1 and cells[cy_clamped][cx_clamped][0]:
                    toggled_border = (cx_clamped, cy_clamped, 'N')
                elif min_d == d_S and cy_clamped == 0 and cells[cy_clamped][cx_clamped][1]:
                    toggled_border = (cx_clamped, cy_clamped, 'S')
                elif min_d == d_E and cx_clamped == width - 1 and cells[cy_clamped][cx_clamped][2]:
                    toggled_border = (cx_clamped, cy_clamped, 'E')
                elif min_d == d_W and cx_clamped == 0 and cells[cy_clamped][cx_clamped][3]:
                    toggled_border = (cx_clamped, cy_clamped, 'W')
                    
                if toggled_border is not None:
                    ent = data_dict.get('entrance')
                    if ent and len(ent) >= 3 and toggled_border == tuple(ent[:3]):
                        data_dict['entrance'] = None
                    if data_dict.get('exits'):
                        data_dict['exits'] = [e for e in data_dict['exits']
                                              if len(e) >= 3 and toggled_border != tuple(e[:3])]
                        
                opened_border = None
                if min_d == d_N and cy_clamped == depth - 1 and not cells[cy_clamped][cx_clamped][0]:
                    opened_border = (cx_clamped, cy_clamped, 'N')
                elif min_d == d_S and cy_clamped == 0 and not cells[cy_clamped][cx_clamped][1]:
                    opened_border = (cx_clamped, cy_clamped, 'S')
                elif min_d == d_E and cx_clamped == width - 1 and not cells[cy_clamped][cx_clamped][2]:
                    opened_border = (cx_clamped, cy_clamped, 'E')
                elif min_d == d_W and cx_clamped == 0 and not cells[cy_clamped][cx_clamped][3]:
                    opened_border = (cx_clamped, cy_clamped, 'W')
                    
                if opened_border is not None:
                    floors = data_dict.get('floors', props.floors)
                    if z_hit == 0:
                        entrance_val = data_dict.get('entrance')
                        if entrance_val and len(entrance_val) >= 3:
                            old_x, old_y, old_d = entrance_val[0], entrance_val[1], entrance_val[2]
                            if 0 <= old_x < width and 0 <= old_y < depth:
                                if old_d == 'N':
                                    cells[old_y][old_x][0] = True
                                    if old_y + 1 < depth:
                                        cells[old_y + 1][old_x][1] = True
                                elif old_d == 'S':
                                    cells[old_y][old_x][1] = True
                                    if old_y - 1 >= 0:
                                        cells[old_y - 1][old_x][0] = True
                                elif old_d == 'E':
                                    cells[old_y][old_x][2] = True
                                    if old_x + 1 < width:
                                        cells[old_y][old_x + 1][3] = True
                                elif old_d == 'W':
                                    cells[old_y][old_x][3] = True
                                    if old_x - 1 >= 0:
                                        cells[old_y][old_x - 1][2] = True
                                self._old_entrance_dirty = (old_x, old_y)
                        data_dict['entrance'] = [cx_clamped, cy_clamped, opened_border[2]]
                    elif z_hit == floors - 1:
                        if 'exits' not in data_dict or data_dict['exits'] is None:
                            data_dict['exits'] = []
                        if not any(ex[0] == cx_clamped and ex[1] == cy_clamped and ex[2] == opened_border[2] for ex in data_dict['exits']):
                            data_dict['exits'].append([cx_clamped, cy_clamped, opened_border[2]])
                            
                data_dict['cells'] = original_cells
                self.report({'INFO'}, f"Toggled wall at cell ({cx_clamped}, {cy_clamped})")
                modified = True
                
        if modified:
            dirty_cells = set()
            for dy in [-1, 0, 1]:
                for dx in [-1, 0, 1]:
                    ny, nx = cy_clamped + dy, cx_clamped + dx
                    if 0 <= ny < depth and 0 <= nx < width:
                        dirty_cells.add((z_hit, ny, nx))
            old_entrance = getattr(self, "_old_entrance_dirty", None)
            if old_entrance is not None:
                old_x, old_y = old_entrance
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        ny, nx = old_y + dy, old_x + dx
                        if 0 <= ny < depth and 0 <= nx < width:
                            dirty_cells.add((z_hit, ny, nx))
                self._old_entrance_dirty = None
            return dirty_cells
        return None

    def modal(self, context, event):
        """Handle viewport events: wall toggle on click, mesh cycle on shift+click, exit on Esc/Enter."""
        props = context.scene.fire_maze
        
        # Check early exit / mouse boundary conditions
        should_exit, exit_value = self._check_modal_early_exit_and_bounds(context, event, props)
        if should_exit:
            return exit_value

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                # Perform the raycast
                obj, loc, normal = self._do_raycast(context, event)
                
                if obj:
                    col_name = getattr(props, "fire_maze_collection_name", "")
                    col = bpy.data.collections.get(col_name) if col_name else None
                    if not col or (obj.name not in col.objects):
                        col = None
                        for c in bpy.data.collections:
                            if "fire_maze_data" in c:
                                if obj.name in c.objects:
                                    col = c
                                    break
                    
                    if not col:
                        col = context.collection
                        if "fire_maze_data" not in col:
                            col = None
                            
                    if col and getattr(self, "maze_data", None) is not None:
                        # Offset hit coordinates slightly inward along normal to prevent floating-point boundary issues
                        if normal:
                            offset_loc = loc - normal * 0.01
                        else:
                            offset_loc = loc
                        
                        ts = props.tile_size
                        tiled = props.wall_height_tiled
                        tiles_high = props.wall_height_tiles if tiled else 1
                        wh = ts * tiles_high if tiled else props.wall_height
                        if props.is_editing:
                            z_hit = props.edit_floor_level
                        else:
                            if normal and abs(normal.z) > 0.5:
                                z_val = loc.z - 0.01 if normal.z < 0 else loc.z + 0.01
                                z_hit = max(0, min(props.floors - 1, int(z_val / wh))) if wh > 0 and props.floors > 0 else 0
                            else:
                                z_hit = max(0, min(props.floors - 1, int(offset_loc.z / wh))) if wh > 0 and props.floors > 0 else 0
                        hit_x, hit_y = offset_loc.x, offset_loc.y
                        
                        data_dict = self._maze_raw
                        width = data_dict['width']
                        depth = data_dict['depth']
                        cells = data_dict['cells']
                        original_cells = cells  # reference alias — preserves the raw 2D grid before cells is reassigned to the hit floor's slice
                        cells_3d, floors = _resolve_cells_3d(cells)
                        z_hit = max(0, min(floors - 1, z_hit))
                        cells = cells_3d[z_hit]
                        wall_mode = data_dict.get('wall_mode', props.wall_mode)
                        
                        grid_type = data_dict.get('grid_type', 'rect')
                        ring_sectors = data_dict.get('ring_sectors')
                        rings = data_dict.get('polar_rings', 5)

                        # Cell coordinates calculation
                        if grid_type == 'polar':
                            r_hit, phi_hit, r_idx, alpha_r, theta, Nr = _get_polar_coords(hit_x, hit_y, ts, ring_sectors)
                            cx_clamped = theta
                            cy_clamped = r_idx
                        else:
                            cx_clamped = max(0, min(math.floor(hit_x / ts), width - 1))
                            cy_clamped = max(0, min(math.floor(hit_y / ts), depth - 1))

                        num_wall_meshes = data_dict.get('num_wall_meshes', 0)
                        num_floor_meshes = data_dict.get('num_floor_meshes', 0)
                        num_roof_meshes = data_dict.get('num_roof_meshes', 0)

                        face_dir = 'WALL'
                        if normal:
                            nx, ny, nz = abs(normal.x), abs(normal.y), abs(normal.z)
                            if nz > nx and nz > ny:
                                tiled = props.wall_height_tiled
                                tiles_high = props.wall_height_tiles if tiled else 1
                                wh = ts * tiles_high if tiled else props.wall_height
                                face_dir = 'ROOF' if loc.z > (z_hit * wh + wh * 0.5) else 'FLOOR'
                            elif grid_type == 'rect':
                                if ny > nx and ny > nz:
                                    face_dir = 'N' if normal.y > 0 else 'S'
                                else:
                                    face_dir = 'E' if normal.x > 0 else 'W'

                        dirty_cells = None
                        if props.edit_tool == 'stair' and props.floors > 1:
                            dirty_cells = self._handle_stair_tool(context, event, props, col, data_dict, z_hit, cx_clamped, cy_clamped, grid_type, original_cells, wall_mode)
                        elif event.shift:
                            if grid_type == 'polar':
                                dirty_cells = self._handle_polar_mesh_cycle(context, event, props, col, data_dict, cells, wall_mode, ring_sectors, rings, hit_x, hit_y, ts, r_hit, phi_hit, r_idx, alpha_r, theta, Nr, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit)
                            else:
                                dirty_cells = self._handle_rect_mesh_cycle(context, event, props, col, data_dict, cells, wall_mode, hit_x, hit_y, ts, width, depth, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit)
                        else:
                            # wall_mode comes from the stored data (line 1727),
                            # not from grid_type — polar mazes can use either
                            # cube or thin walls, and _handle_polar_wall_toggle
                            # supports both branches.
                            if grid_type == 'polar':
                                dirty_cells = self._handle_polar_wall_toggle(context, event, props, col, data_dict, cells, wall_mode, ring_sectors, rings, hit_x, hit_y, ts, r_hit, phi_hit, r_idx, alpha_r, theta, Nr, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit)
                            else:
                                dirty_cells = self._handle_rect_wall_toggle(context, event, props, col, data_dict, cells, wall_mode, hit_x, hit_y, ts, width, depth, face_dir, num_wall_meshes, num_floor_meshes, num_roof_meshes, original_cells, z_hit)
                        
                        if dirty_cells:
                            self._is_dirty = True
                            self.maze_data.entrance = tuple(data_dict['entrance']) if data_dict.get('entrance') else None
                            self.maze_data.exits = [tuple(e) for e in data_dict.get('exits', [])]
                            self.maze_data.cells = data_dict['cells']
                            self.maze_data.stairs = data_dict.get('stairs', [])
                            self.maze_data.guide_path = find_shortest_path(self.maze_data, wall_mode=wall_mode)
                            # Guide path recomputed on every edit deliberately — the user sees
                            # the green/red path update immediately, giving live connectivity
                            # feedback without needing to exit edit mode.
                            # Cost: O(W×H) BFS per click, acceptable for typical maze sizes.
                            # If performance becomes an issue, consider debounce rather than
                            # defer-to-exit, since the live feedback is important UX.
                            data_dict['guide_path'] = self.maze_data.guide_path
                            
                            from .mesh_builder import rebuild_maze_incrementally
                            col["fire_maze_data"] = json.dumps(data_dict)
                            rebuild_maze_incrementally(props, self.maze_data, context, col, dirty_cells)
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        """Enter interactive edit mode: rebuild helper, register modal handler, or exit if already editing."""
        props = context.scene.fire_maze
        
        # If already editing, act as a toggle off
        if props.is_editing:
            props.is_editing = False
            context.workspace.status_text_set(None)
            delete_edit_helper()
            set_other_mazes_visibility(context, True)
            col = _get_active_maze_collection(context)
            if col:
                rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        col = _get_active_maze_collection(context)
                
        if not col:
            self.report({'ERROR'}, "No generated maze found to edit. Please generate a maze first.")
            return {'CANCELLED'}
            
        props.is_editing = True
        props.fire_maze_collection_name = col.name
        set_other_mazes_visibility(context, False)
        self._old_entrance_dirty = None
        self._is_dirty = False

        try:
            data = json.loads(col["fire_maze_data"])
            self._maze_raw = data
            self.maze_data = MazeData(
                width=data['width'],
                depth=data['depth'],
                cells=data['cells'],
                entrance=tuple(data['entrance']) if data.get('entrance') else None,
                exits=[tuple(e) for e in data.get('exits', [])],
                center=tuple(data['center']),
                grid_type=data.get('grid_type', 'rect'),
                polar_rings=data.get('polar_rings', 0),
                ring_sectors=data.get('ring_sectors', []),
                floors=data.get('floors', 1),
                stairs=data.get('stairs', []),
            )
            col_floors = self.maze_data.floors
            props.edit_floor_level = max(0, min(props.edit_floor_level, col_floors - 1))
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.debug(f"Failed to validate floor level or parse maze data: {e}")
            self.maze_data = None
            self._maze_raw = None
            props.is_editing = False
            context.workspace.status_text_set(None)
            set_other_mazes_visibility(context, True)
            return {'CANCELLED'}

        rebuild_maze_from_collection(context, col)
        try:
            data = json.loads(col["fire_maze_data"])
            self._maze_raw = data
            self.maze_data = MazeData(
                width=data['width'],
                depth=data['depth'],
                cells=data['cells'],
                entrance=tuple(data['entrance']) if data.get('entrance') else None,
                exits=[tuple(e) for e in data.get('exits', [])],
                center=tuple(data['center']),
                grid_type=data.get('grid_type', 'rect'),
                polar_rings=data.get('polar_rings', 0),
                ring_sectors=data.get('ring_sectors', []),
                floors=data.get('floors', 1),
                stairs=data.get('stairs', []),
            )
            col_floors = self.maze_data.floors
            props.edit_floor_level = max(0, min(props.edit_floor_level, col_floors - 1))
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.debug(f"Failed to reload maze data after rebuild: {e}")
        if props.floors > 1:
            context.workspace.status_text_set(f"FireMaze Editor ({props.floors} floors): Left-click walls to toggle. Shift+click to cycle mesh. Enter/Esc to exit.")
        else:
            context.workspace.status_text_set("FireMaze Editor: Left-click walls to toggle. Shift+Left-click to cycle mesh. Enter/Esc to exit.")
        self.init_workspace = context.workspace.name
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _generate_maze_image_datablock(context, img_name="FireMaze_Layout"):
    """Create a Blender Image datablock from the current maze layout (rectangular grids only)."""
    col = _get_active_maze_collection(context)
    if not col or "fire_maze_data" not in col:
        return None, "No active maze layout found. Please generate a maze first."

    try:
        data = json.loads(col["fire_maze_data"])

        if data.get('grid_type', 'rect') == 'polar':
            return None, "Save Maze as Image is currently only supported for Rectangular mazes."
        width = data["width"]
        depth = data["depth"]
        cells = data["cells"]
        wall_mode = data.get("wall_mode", "thin")
    except Exception as e:
        return None, f"Failed to read maze data: {e}"

    # Remove existing image if it exists to refresh
    old_img = bpy.data.images.get(img_name)
    if old_img:
        bpy.data.images.remove(old_img)

    # Create new image block
    if wall_mode == 'cube':
        img_w, img_h = width, depth
    else: # thin mode, upscale 3x
        img_w, img_h = width * 3, depth * 3

    img = bpy.data.images.new(name=img_name, width=img_w, height=img_h, alpha=False, float_buffer=False)

    # Buffer size is img_w * img_h * 4 (RGBA channels)
    pixels = [0.0] * (img_w * img_h * 4)

    cells_3d, floors = _resolve_cells_3d(cells)
    floor_cells = cells_3d[0]

    if wall_mode == 'cube':
        for y in range(depth):
            for x in range(width):
                is_wall = floor_cells[y][x][0]
                val = 0.0 if is_wall else 1.0
                idx = (y * img_w + x) * 4
                pixels[idx] = val
                pixels[idx + 1] = val
                pixels[idx + 2] = val
                pixels[idx + 3] = 1.0
    else: # thin mode
        for y in range(depth):
            for x in range(width):
                c = floor_cells[y][x]
                bx, by = x * 3, y * 3
                
                # Center is walkable
                c_idx = ((by + 1) * img_w + (bx + 1)) * 4
                pixels[c_idx] = pixels[c_idx+1] = pixels[c_idx+2] = 1.0
                pixels[c_idx+3] = 1.0
                
                # North (no North wall)
                if not c[0]:
                    n_idx = ((by + 2) * img_w + (bx + 1)) * 4
                    pixels[n_idx] = pixels[n_idx+1] = pixels[n_idx+2] = 1.0
                    pixels[n_idx+3] = 1.0
                    
                # South (no South wall)
                if not c[1]:
                    s_idx = (by * img_w + (bx + 1)) * 4
                    pixels[s_idx] = pixels[s_idx+1] = pixels[s_idx+2] = 1.0
                    pixels[s_idx+3] = 1.0
                    
                # East (no East wall)
                if not c[2]:
                    e_idx = ((by + 1) * img_w + (bx + 2)) * 4
                    pixels[e_idx] = pixels[e_idx+1] = pixels[e_idx+2] = 1.0
                    pixels[e_idx+3] = 1.0
                    
                # West (no West wall)
                if not c[3]:
                    w_idx = ((by + 1) * img_w + bx) * 4
                    pixels[w_idx] = pixels[w_idx+1] = pixels[w_idx+2] = 1.0
                    pixels[w_idx+3] = 1.0

    # Set pixel data
    img.pixels.foreach_set(pixels)
    img.update()
    return img, None



class MAZE_OT_save_as_image(bpy.types.Operator):
    """Export the current maze layout as a black-and-white Blender Image datablock."""

    bl_idname = "fire_maze.save_as_image"
    bl_label = "Save Maze as Image"
    bl_description = "Export the current maze layout as a black-and-white image in the Blender database"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """Generate the image datablock and report its name."""
        img, err = _generate_maze_image_datablock(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved maze layout as image '{img.name}' (Size: {img.size[0]}x{img.size[1]})")
        return {'FINISHED'}


class MAZE_OT_save_image_file(bpy.types.Operator, ExportHelper):
    """Save the current maze layout as a PNG file on disk via a file dialog."""

    bl_idname = "fire_maze.save_image_file"
    bl_label = "Save Maze Image to Disk"
    bl_description = "Save the current maze layout as a PNG image file on disk"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        """Generate a temp image, save to the chosen file path, and clean up."""
        img, err = _generate_maze_image_datablock(context, img_name="FireMaze_Export_Temp")
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        
        try:
            img.filepath_raw = self.filepath
            img.file_format = 'PNG'
            img.save()
            bpy.data.images.remove(img)
            self.report({'INFO'}, f"Saved maze image successfully to: {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            try:
                bpy.data.images.remove(img)
            except Exception as remove_err:
                pass
            self.report({'ERROR'}, f"Failed to save image file: {e}")
            return {'CANCELLED'}


class MAZE_OT_load_mask_image(bpy.types.Operator, ImportHelper):
    """Load a black-and-white image from disk to use as a maze shape mask."""

    bl_idname = "fire_maze.load_mask_image"
    bl_label = "Load Mask Image"
    bl_description = "Load a black-and-white image from disk to use as a maze mask"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".png"
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.bmp;*.tga",
        options={'HIDDEN'},
        maxlen=255,
    )
    
    def execute(self, context):
        """Load the selected image file and assign it to the mask_image property."""
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}
        try:
            img = bpy.data.images.load(self.filepath)
            context.scene.fire_maze.mask_image = img
            self.report({'INFO'}, f"Loaded mask image: {img.name}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load image: {e}")
            return {'CANCELLED'}


class MAZE_OT_restore_autosave(bpy.types.Operator):
    """Restore maze settings and layout from the temporary autosave file."""

    bl_idname = "fire_maze.restore_autosave"
    bl_label = "Restore Last Session"
    bl_description = "Restore maze settings and layout from the last session's autosave"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """Read the autosave JSON, deserialize it, and acknowledge the recovery."""
        global show_recovery_warning, _has_autosave_cached
        
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        if not os.path.exists(autosave_path):
            self.report({'ERROR'}, "No autosave file found")
            return {'CANCELLED'}
            
        try:
            with open(autosave_path, 'r') as f:
                data = json.load(f)
                
            _deserialize_session_data(context, data)
            
            # Persist an acknowledgement in the sidecar ack file
            try:
                with open(AUTOSAVE_ACK_PATH, 'w') as f:
                    json.dump(data, f, indent=2)
            except Exception as ack_err:
                self.report({'WARNING'}, f"Failed to write autosave ack: {ack_err}")
            
            show_recovery_warning = False
            _has_autosave_cached = False
            
            if data.get("maze_data"):
                self.report({'INFO'}, "Successfully restored maze session from autosave")
            else:
                self.report({'INFO'}, "Restored settings from autosave (no maze layout was saved)")
                
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to restore autosave: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


class MAZE_OT_discard_autosave(bpy.types.Operator):
    """Delete the temporary autosave and acknowledgement files from disk."""

    bl_idname = "fire_maze.discard_autosave"
    bl_label = "Discard Recovery Data"
    bl_description = "Delete the temporary autosave recovery file from disk"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        """Remove autosave files, clear global flags, and report the result."""
        global show_recovery_warning, _has_autosave_cached
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        ack_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave_ack.json")

        
        file_removed = False
        try:
            if os.path.exists(autosave_path):
                os.remove(autosave_path)
                file_removed = True
            if os.path.exists(ack_path):
                os.remove(ack_path)
                
            show_recovery_warning = False
            _has_autosave_cached = False
            
            if file_removed:
                self.report({'INFO'}, "Autosave recovery file discarded successfully")
            else:
                self.report({'INFO'}, "No recovery file found to discard")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to discard recovery files: {e}")
            return {'CANCELLED'}

class MAZE_OT_save_session(bpy.types.Operator, ExportHelper):
    """Save current maze settings and layout to a JSON file via a file dialog."""

    bl_idname = "fire_maze.save_session"
    bl_label = "Save Session"
    bl_description = "Save current maze settings and layout to a JSON file"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )
    
    def execute(self, context):
        """Serialize session data and write to the chosen file path."""
        try:
            payload = _serialize_session_data(context)
            payload_str = json.dumps(payload, indent=2)
            with open(self.filepath, 'w') as f:
                f.write(payload_str)
            self.report({'INFO'}, f"Session saved successfully to: {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save session: {e}")
            return {'CANCELLED'}

class MAZE_OT_load_session(bpy.types.Operator, ImportHelper):
    """Load maze settings and layout from a JSON file via a file dialog."""

    bl_idname = "fire_maze.load_session"
    bl_label = "Load Session"
    bl_description = "Load maze settings and layout from a JSON file"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )
    
    def execute(self, context):
        """Read the selected JSON file, deserialize it, and rebuild the maze."""
        global show_recovery_warning
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
            _deserialize_session_data(context, data)
            show_recovery_warning = False
            self.report({'INFO'}, f"Session loaded successfully from: {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load session: {e}")
            return {'CANCELLED'}

classes = (
    MAZE_OT_generate,
    MAZE_OT_clear,
    MAZE_OT_interactive_edit,
    MAZE_OT_save_as_image,
    MAZE_OT_save_image_file,
    MAZE_OT_load_mask_image,
    MAZE_OT_restore_autosave,
    MAZE_OT_discard_autosave,
    MAZE_OT_save_session,
    MAZE_OT_load_session,
)

def register():
    """Register all FireMaze operator classes."""
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    """Unregister all FireMaze operator classes."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
