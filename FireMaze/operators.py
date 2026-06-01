import bpy
import json
import random
import math
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from .maze_generator import generate_maze, find_shortest_path, MazeData
from .mesh_builder import build_maze_objects

show_recovery_warning = True

def _find_or_create_maze_collection(base_name):
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
    for col in list(bpy.data.collections):
        if col.name.startswith("FireMaze"):
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
    helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
    if helper:
        data = helper.data
        bpy.data.objects.remove(helper, do_unlink=True)
        if data and data.users == 0:
            bpy.data.meshes.remove(data)

def _serialize_session_data(context):
    props = context.scene.fire_maze
    props_data = {}
    prop_names = [
        "width", "depth", "wall_height", "wall_height_tiled", "wall_height_tiles",
        "wall_thickness", "tile_size", "wall_mode", "grid_type", "polar_rings",
        "polar_custom_alignment", "mode", "emergency_exits", "seed", "tiles_centered",
        "algorithm", "rooms_enable", "rooms_count", "min_room_size", "max_room_size",
        "loop_probability", "isolated_wall_prob", "entrance_side", "exit_side",
        "num_entrances", "num_exits", "cube_mode_pillar", "generate_guide",
        "guide_type", "guide_width", "guide_height_offset", "guide_wave_amplitude",
        "guide_wave_frequency", "wall_translate", "wall_rotate", "wall_scale",
        "floor_translate", "floor_rotate", "floor_scale", "single_wall_object",
        "merge_objects", "remove_doubles", "generate_lightmap", "lightmap_method",
        "generate_colliders", "merge_colliders", "optimize_colliders_coplanar", "optimize_coplanar", "vertex_paint_enable",
        "vertex_paint_mode", "vertex_paint_intensity", "prop_torch_density", "prop_chest_density",
        "mask_invert"
    ]
    for name in prop_names:
        val = getattr(props, name, None)
        if val is not None:
            if isinstance(val, (int, float, str, bool)):
                props_data[name] = val
            elif hasattr(val, "copy") or isinstance(val, (list, tuple)):
                props_data[name] = list(val)
    
    pointer_props = [
        "custom_floor_mesh", "custom_wall_north", "custom_wall_south",
        "custom_wall_east", "custom_wall_west", "custom_roof_mesh",
        "custom_wall_collection", "custom_floor_collection", "custom_roof_collection",
        "prop_torch_mesh", "prop_chest_mesh", "prop_door_mesh", "mask_image"
    ]
    for name in pointer_props:
        ref = getattr(props, name, None)
        if ref:
            props_data[name] = ref.name
            
    maze_json = None
    col = bpy.data.collections.get("FireMaze")
    if col and "fire_maze_data" in col:
        maze_json = col["fire_maze_data"]
        
    return {
        "properties": props_data,
        "maze_data": maze_json
    }

def _deserialize_session_data(context, data):
    props = context.scene.fire_maze
    properties = data.get("properties", {})
    maze_json = data.get("maze_data")
    
    prop_names = [
        "width", "depth", "wall_height", "wall_height_tiled", "wall_height_tiles",
        "wall_thickness", "tile_size", "wall_mode", "grid_type", "polar_rings",
        "polar_custom_alignment", "mode", "emergency_exits", "seed", "tiles_centered",
        "algorithm", "rooms_enable", "rooms_count", "min_room_size", "max_room_size",
        "loop_probability", "isolated_wall_prob", "entrance_side", "exit_side",
        "num_entrances", "num_exits", "cube_mode_pillar", "generate_guide",
        "guide_type", "guide_width", "guide_height_offset", "guide_wave_amplitude",
        "guide_wave_frequency", "wall_translate", "wall_rotate", "wall_scale",
        "floor_translate", "floor_rotate", "floor_scale", "single_wall_object",
        "merge_objects", "remove_doubles", "generate_lightmap", "lightmap_method",
        "generate_colliders", "merge_colliders", "optimize_colliders_coplanar", "optimize_coplanar", "vertex_paint_enable",
        "vertex_paint_mode", "vertex_paint_intensity", "prop_torch_density", "prop_chest_density",
        "mask_invert"
    ]
    for name in prop_names:
        if name in properties:
            val = properties[name]
            if isinstance(val, list):
                val = tuple(val)
            try:
                setattr(props, name, val)
            except Exception as ex:
                print(f"Failed to set property {name}: {ex}")
                
    pointer_props = [
        "custom_floor_mesh", "custom_wall_north", "custom_wall_south",
        "custom_wall_east", "custom_wall_west", "custom_roof_mesh",
        "custom_wall_collection", "custom_floor_collection", "custom_roof_collection",
        "prop_torch_mesh", "prop_chest_mesh", "prop_door_mesh", "mask_image"
    ]
    for name in pointer_props:
        if name in properties:
            val = properties[name]
            ref = None
            if name == "mask_image":
                ref = bpy.data.images.get(val)
            elif name in {"custom_wall_collection", "custom_floor_collection", "custom_roof_collection"}:
                ref = bpy.data.collections.get(val)
            elif name in {"custom_floor_mesh", "custom_wall_north", "custom_wall_south", "custom_wall_east", "custom_wall_west", "custom_roof_mesh"}:
                ref = bpy.data.meshes.get(val)
            else:
                ref = bpy.data.objects.get(val)
            if ref or val is None:
                setattr(props, name, ref)
                
    if maze_json:
        col = bpy.data.collections.get("FireMaze")
        if not col:
            col = bpy.data.collections.new("FireMaze")
            context.scene.collection.children.link(col)
        col["fire_maze_data"] = maze_json
        rebuild_maze_from_collection(context, col)

def save_autosave(context):
    import os
    import tempfile
    import json
    import threading
    try:
        payload = _serialize_session_data(context)
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        payload_str = json.dumps(payload, indent=2)
        
        def write_worker(path, data_str):
            import uuid
            temp_path = path + "." + uuid.uuid4().hex + ".tmp"
            try:
                with open(temp_path, 'w') as f:
                    f.write(data_str)
                try:
                    os.replace(temp_path, path)
                except PermissionError:
                    pass
                except Exception as ex:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
            except Exception as e:
                print("FireMaze: Background autosave write failed:", e)
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                
        threading.Thread(target=write_worker, args=(autosave_path, payload_str), daemon=True).start()
    except Exception as e:
        print("FireMaze: Failed to initiate autosave:", e)

def rebuild_maze_from_collection(context, col):
    if "fire_maze_data" not in col:
        return
        
    data_dict = json.loads(col["fire_maze_data"])
    wall_mode = data_dict.get('wall_mode', context.scene.fire_maze.wall_mode)
    props = context.scene.fire_maze
    props.wall_mode = wall_mode

    # Determine number of meshes in collections
    num_wall_meshes = 0
    if props.custom_wall_collection:
        num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

    num_floor_meshes = 0
    if props.custom_floor_collection:
        num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

    num_roof_meshes = 0
    if props.custom_roof_collection:
        num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])
    
    grid_type = data_dict.get('grid_type')
    if not grid_type:
        grid_type = 'polar' if 'ring_sectors' in data_dict else 'rect'
    polar_rings = data_dict.get('polar_rings', 0)
    ring_sectors = data_dict.get('ring_sectors', [])

    maze_data = MazeData(
        width=data_dict['width'],
        depth=data_dict['depth'],
        cells=data_dict['cells'],
        entrance=tuple(data_dict['entrance']) if data_dict['entrance'] else None,
        exits=[tuple(e) for e in data_dict['exits']],
        center=tuple(data_dict['center']),
        guide_path=[tuple(gp) for gp in data_dict.get('guide_path', [])],
        grid_type=grid_type,
        polar_rings=polar_rings,
        ring_sectors=ring_sectors
    )

    # Recompute guide path
    maze_data.guide_path = find_shortest_path(maze_data, wall_mode=wall_mode)

    # Update stored JSON
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
    })

    save_autosave(context)

    # Clear old maze objects from this collection
    for obj in list(col.objects):
        if obj.get("fire_maze"):
            data = obj.data
            obj_type = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if obj_type == 'MESH':
                    try:
                        bpy.data.meshes.remove(data)
                    except Exception:
                        pass
                elif obj_type == 'CURVE':
                    try:
                        bpy.data.curves.remove(data)
                    except Exception:
                        pass

    # Sweep any orphaned FireMaze meshes/curves left over in the database
    for m in list(bpy.data.meshes):
        if m.name.startswith("FireMaze") and m.users == 0:
            try:
                bpy.data.meshes.remove(m)
            except Exception:
                pass
    for c in list(bpy.data.curves):
        if c.name.startswith("FireMaze") and c.users == 0:
            try:
                bpy.data.curves.remove(c)
            except Exception:
                pass

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
    scene = context.scene
    region = context.region
    rv3d = context.region_data
    coord = event.mouse_region_x, event.mouse_region_y

    view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    
    depsgraph = context.evaluated_depsgraph_get()
    result, location, normal, index, object, matrix = scene.ray_cast(depsgraph, ray_origin, view_vector)
    
    if result:
        return object, location, normal
    return None, None, None

class MAZE_OT_generate(bpy.types.Operator):
    bl_idname = "fire_maze.generate"
    bl_label = "Generate Maze"
    bl_description = "Generate a new maze from current settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
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
        if props.custom_wall_collection:
            num_wall_meshes = len([o for o in props.custom_wall_collection.objects if o.type == 'MESH'])

        num_floor_meshes = 0
        if props.custom_floor_collection:
            num_floor_meshes = len([o for o in props.custom_floor_collection.objects if o.type == 'MESH'])

        num_roof_meshes = 0
        if props.custom_roof_collection:
            num_roof_meshes = len([o for o in props.custom_roof_collection.objects if o.type == 'MESH'])

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
            mask_image=props.mask_image,
            mask_invert=props.mask_invert,
            grid_type=props.grid_type,
            polar_rings=props.polar_rings,
        )

        col = _find_or_create_maze_collection("FireMaze")
        
        # Serialize and store maze data on the collection
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
        })

        build_maze_objects(props, maze_data, context, collection=col)
        if props.generate_colliders:
            build_maze_objects(props, maze_data, context, collection=col, force_simple=True, name_suffix="_Collider")

        save_autosave(context)

        if props.grid_type == 'rect':
            self.report({'INFO'}, f"Maze generated ({props.width}x{props.depth})")
        else:
            self.report({'INFO'}, f"Polar maze generated ({props.polar_rings} rings)")
        return {'FINISHED'}

class MAZE_OT_clear(bpy.types.Operator):
    bl_idname = "fire_maze.clear"
    bl_label = "Clear Maze"
    bl_description = "Delete all generated maze objects from the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = 0
        for obj in list(bpy.data.objects):
            if obj.get("fire_maze"):
                data = obj.data
                obj_type = obj.type
                bpy.data.objects.remove(obj, do_unlink=True)
                if data and data.users == 0:
                    if obj_type == 'MESH':
                        bpy.data.meshes.remove(data)
                    elif obj_type == 'CURVE':
                        bpy.data.curves.remove(data)
                count += 1

        _remove_maze_collections()

        # Sweep any orphaned FireMaze meshes/curves left over in the database
        for m in list(bpy.data.meshes):
            if m.name.startswith("FireMaze") and m.users == 0:
                try:
                    bpy.data.meshes.remove(m)
                except Exception:
                    pass
        for c in list(bpy.data.curves):
            if c.name.startswith("FireMaze") and c.users == 0:
                try:
                    bpy.data.curves.remove(c)
                except Exception:
                    pass

        self.report({'INFO'}, f"Removed {count} maze object(s)")
        return {'FINISHED'}

class MAZE_OT_interactive_edit(bpy.types.Operator):
    bl_idname = "fire_maze.interactive_edit"
    bl_label = "Interactive Maze Editor"
    bl_description = "Left-click on walls in the 3D viewport to toggle them on/off"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.fire_maze
        return props.grid_type in {'rect', 'polar'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        props = context.scene.fire_maze
        # Clean exit if toggled off by another button click
        if not props.is_editing:
            context.workspace.status_text_set(None)
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        # Check if the mouse is within the main 3D viewport WINDOW region coordinates.
        # If the mouse is outside (e.g., in the Menus, Header, or File/Edit menus),
        # we pass the event through so the user can interact with the Blender UI normally.
        if context.region:
            rx_min = context.region.x
            rx_max = rx_min + context.region.width
            ry_min = context.region.y
            ry_max = ry_min + context.region.height
            if not (rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max):
                return {'PASS_THROUGH'}

        # Check if the mouse is inside overlay regions within the viewport area
        # (e.g., the UI/Sidebar N-panel or the Tools shelf T-panel)
        if context.area:
            for region in context.area.regions:
                if region.type != 'WINDOW':
                    rx_min = region.x
                    rx_max = rx_min + region.width
                    ry_min = region.y
                    ry_max = ry_min + region.height
                    if rx_min <= event.mouse_x <= rx_max and ry_min <= event.mouse_y <= ry_max:
                        return {'PASS_THROUGH'}

        if event.type in {'RET', 'NUMPAD_ENTER', 'ESC'}:
            context.workspace.status_text_set(None)
            props.is_editing = False
            delete_edit_helper()
            col = None
            for c in bpy.data.collections:
                if "fire_maze_data" in c:
                    col = c
                    break
            if col:
                rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                # Unhide the helper object temporarily
                helper = bpy.data.objects.get("_FireMaze_Edit_Helper")
                if helper:
                    helper.hide_viewport = False
                
                # Temporarily hide all non-helper maze objects to ensure we raycast against the simple helper mesh
                hidden_objs = []
                for o in list(bpy.data.objects):
                    if o.get("fire_maze") and o.name != "_FireMaze_Edit_Helper":
                        if not o.hide_viewport:
                            o.hide_viewport = True
                            hidden_objs.append(o)
                
                # Force view_layer update so the depsgraph evaluates the visibility changes before raycasting!
                context.view_layer.update()
                
                # Perform the raycast
                obj, loc, normal = _raycast_from_mouse(context, event)
                
                # Immediately restore visibility
                if helper:
                    helper.hide_viewport = True
                for o in hidden_objs:
                    o.hide_viewport = False
                
                # Force view_layer update to restore viewport state
                context.view_layer.update()
                
                if obj:
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
                            
                    if col:
                        ts = props.tile_size
                        # Offset hit coordinates slightly inward along normal to prevent floating-point boundary issues
                        if normal:
                            offset_loc = loc - normal * 0.01
                        else:
                            offset_loc = loc
                        hit_x, hit_y = offset_loc.x, offset_loc.y
                        
                        data_dict = json.loads(col["fire_maze_data"])
                        width = data_dict['width']
                        depth = data_dict['depth']
                        cells = data_dict['cells']
                        wall_mode = data_dict.get('wall_mode', props.wall_mode)
                        
                        num_wall_meshes = data_dict.get('num_wall_meshes', 0)
                        num_floor_meshes = data_dict.get('num_floor_meshes', 0)
                        num_roof_meshes = data_dict.get('num_roof_meshes', 0)

                        grid_type = data_dict.get('grid_type', 'rect')
                        ring_sectors = data_dict.get('ring_sectors')
                        rings = data_dict.get('polar_rings', 5)

                        face_dir = 'WALL'
                        if normal:
                            nx, ny, nz = abs(normal.x), abs(normal.y), abs(normal.z)
                            if nz > nx and nz > ny:
                                tiled = props.wall_height_tiled
                                tiles_high = props.wall_height_tiles if tiled else 1
                                wh = ts * tiles_high if tiled else props.wall_height
                                face_dir = 'ROOF' if loc.z > (wh * 0.5) else 'FLOOR'
                            elif grid_type == 'rect':
                                if ny > nx and ny > nz:
                                    face_dir = 'N' if normal.y > 0 else 'S'
                                else:
                                    face_dir = 'E' if normal.x > 0 else 'W'

                        if event.shift:
                            if grid_type == 'polar':
                                r_hit = math.sqrt(hit_x**2 + hit_y**2)
                                phi_hit = math.atan2(hit_y, hit_x)
                                if phi_hit < 0:
                                    phi_hit += 2 * math.pi
                                    
                                r_idx = math.floor(r_hit / ts + 0.5)
                                r_idx = max(0, min(r_idx, rings - 1))
                                
                                Nr = ring_sectors[r_idx]
                                alpha_r = 2 * math.pi / Nr
                                theta = math.floor(phi_hit / alpha_r)
                                theta = max(0, min(theta, Nr - 1))
                                
                                modified = False
                                rebuilt_text = ""
                                
                                if face_dir == 'ROOF':
                                    roof_idx_pos = 5 if len(cells[r_idx][theta]) >= 8 else 5
                                    if num_roof_meshes > 0 and len(cells[r_idx][theta]) > roof_idx_pos:
                                        current_idx = cells[r_idx][theta][roof_idx_pos] if isinstance(cells[r_idx][theta][roof_idx_pos], int) else -1
                                        cells[r_idx][theta][roof_idx_pos] = (current_idx + 1) % num_roof_meshes
                                        modified = True
                                        rebuilt_text = "roof"
                                elif face_dir == 'FLOOR':
                                    floor_idx_pos = 4 if len(cells[r_idx][theta]) >= 8 else 4
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
                                                
                                                def angle_diff(a, b):
                                                    diff = (a - b) % (2 * math.pi)
                                                    if diff > math.pi:
                                                        diff -= 2 * math.pi
                                                    return abs(diff)
                                                    
                                                d_cw = r_hit * angle_diff(phi_hit, (theta + 1) * alpha_r)
                                                d_ccw = r_hit * angle_diff(phi_hit, theta * alpha_r)
                                                
                                                if r_idx == 0:
                                                    min_d = d_out
                                                else:
                                                    min_d = min(d_in, d_out, d_cw, d_ccw)
                                                    
                                                # Cube mode: find the owner path cell (is_wall == False) of the clicked boundary
                                                # Cells on each side of the boundary: A = (r_idx, theta)
                                                # We determine B (the neighbor cell) and the boundary type
                                                owner_cell = None
                                                owner_idx_pos = -1
                                                boundary_name = ""
                                                
                                                if min_d == d_cw:
                                                    # Clockwise boundary
                                                    A = (r_idx, theta)
                                                    B = (r_idx, (theta + 1) % Nr)
                                                    # A is CW boundary, B is CCW boundary
                                                    if not cells[A[0]][A[1]][0]: # A is path
                                                        owner_cell = A
                                                        owner_idx_pos = 2 # CW index
                                                        boundary_name = "clockwise radial boundary wall"
                                                    elif not cells[B[0]][B[1]][0]: # B is path
                                                        owner_cell = B
                                                        owner_idx_pos = 3 # CCW index
                                                        boundary_name = "counter-clockwise radial boundary wall"
                                                    else:
                                                        owner_cell = A
                                                        owner_idx_pos = 2
                                                        boundary_name = "clockwise radial boundary wall (fallback)"
                                                elif min_d == d_ccw:
                                                    # Counter-clockwise boundary
                                                    A = (r_idx, theta)
                                                    B = (r_idx, (theta - 1) % Nr)
                                                    # A is CCW boundary, B is CW boundary
                                                    if not cells[A[0]][A[1]][0]: # A is path
                                                        owner_cell = A
                                                        owner_idx_pos = 3 # CCW index
                                                        boundary_name = "counter-clockwise radial boundary wall"
                                                    elif not cells[B[0]][B[1]][0]: # B is path
                                                        owner_cell = B
                                                        owner_idx_pos = 2 # CW index
                                                        boundary_name = "clockwise radial boundary wall"
                                                    else:
                                                        owner_cell = A
                                                        owner_idx_pos = 3
                                                        boundary_name = "counter-clockwise radial boundary wall (fallback)"
                                                elif min_d == d_in:
                                                    # Inward boundary
                                                    if r_idx > 0:
                                                        N_in = ring_sectors[r_idx - 1]
                                                        theta_in = 0 if N_in == 1 else (theta if N_in == Nr else theta // 2)
                                                        A = (r_idx, theta)
                                                        B = (r_idx - 1, theta_in)
                                                        # A is IN boundary, B is OUT boundary
                                                        if not cells[A[0]][A[1]][0]: # A is path
                                                            owner_cell = A
                                                            owner_idx_pos = 7 # IN index
                                                            boundary_name = "inward angular boundary wall"
                                                        elif not cells[B[0]][B[1]][0]: # B is path
                                                            owner_cell = B
                                                            owner_idx_pos = 8 # OUT index (from inner ring's perspective)
                                                            boundary_name = "outward angular boundary wall"
                                                        else:
                                                            owner_cell = A
                                                            owner_idx_pos = 7
                                                            boundary_name = "inward angular boundary wall (fallback)"
                                                else: # d_out
                                                    # Outward boundary
                                                    if r_idx < rings - 1:
                                                        N_out = ring_sectors[r_idx + 1]
                                                        theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                                                        theta_out = max(0, min(theta_out, N_out - 1))
                                                        A = (r_idx, theta)
                                                        B = (r_idx + 1, theta_out)
                                                        # A is OUT boundary, B is IN boundary
                                                        if not cells[A[0]][A[1]][0]: # A is path
                                                            owner_cell = A
                                                            owner_idx_pos = 8 # OUT index
                                                            boundary_name = "outward angular boundary wall"
                                                        elif not cells[B[0]][B[1]][0]: # B is path
                                                            owner_cell = B
                                                            owner_idx_pos = 7 # IN index
                                                            boundary_name = "inward angular boundary wall"
                                                        else:
                                                            owner_cell = A
                                                            owner_idx_pos = 8
                                                            boundary_name = "outward angular boundary wall (fallback)"
                                                    else:
                                                        # Outermost boundary: only cell A exists
                                                        owner_cell = (r_idx, theta)
                                                        owner_idx_pos = 6 # Outermost OUT index
                                                        boundary_name = "outermost outward angular boundary wall"
                                                
                                                if owner_cell is not None:
                                                    r_own, theta_own = owner_cell
                                                    # Map indices for backward compatibility (cells of length < 8)
                                                    if len(cells[r_own][theta_own]) < 8:
                                                        if owner_idx_pos in {2, 3}:
                                                            owner_idx_pos = 2 # CW and CCW map to index 2
                                                        elif owner_idx_pos in {6, 7, 8}:
                                                            owner_idx_pos = 3 # IN, OUT, and outermost map to index 3
                                                    
                                                    current_idx = cells[r_own][theta_own][owner_idx_pos] if isinstance(cells[r_own][theta_own][owner_idx_pos], int) else -1
                                                    cells[r_own][theta_own][owner_idx_pos] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = boundary_name
                                                    # Set coordinates for correct console logging
                                                    r_idx, theta = r_own, theta_own
                                    else:
                                        # Thin wall mode
                                        d_in = abs(r_hit - (r_idx - 0.5) * ts)
                                        d_out = abs(r_hit - (r_idx + 0.5) * ts)
                                        
                                        def angle_diff(a, b):
                                            diff = (a - b) % (2 * math.pi)
                                            if diff > math.pi:
                                                diff -= 2 * math.pi
                                            return abs(diff)
                                            
                                        d_cw = r_hit * angle_diff(phi_hit, (theta + 1) * alpha_r)
                                        d_ccw = r_hit * angle_diff(phi_hit, theta * alpha_r)
                                        
                                        if r_idx == 0:
                                            min_d = d_out
                                        else:
                                            min_d = min(d_in, d_out, d_cw, d_ccw)
                                            
                                        if min_d == d_cw:
                                            if num_wall_meshes > 0:
                                                current_idx = cells[r_idx][theta][2] if isinstance(cells[r_idx][theta][2], int) else -1
                                                cells[r_idx][theta][2] = (current_idx + 1) % num_wall_meshes
                                                modified = True
                                                rebuilt_text = "clockwise wall"
                                        elif min_d == d_ccw:
                                            if num_wall_meshes > 0:
                                                prev_theta = (theta - 1) % Nr
                                                current_idx = cells[r_idx][prev_theta][2] if isinstance(cells[r_idx][prev_theta][2], int) else -1
                                                cells[r_idx][prev_theta][2] = (current_idx + 1) % num_wall_meshes
                                                modified = True
                                                rebuilt_text = "counter-clockwise wall"
                                        elif min_d == d_in:
                                            if num_wall_meshes > 0:
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
                                                    current_idx = cells[r_idx + 1][theta_out][3] if isinstance(cells[r_idx + 1][theta_out][3], int) else -1
                                                    cells[r_idx + 1][theta_out][3] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = "outward wall"
                                                else:
                                                    # Outermost boundary wall is stored in the cell itself at index 6
                                                    current_idx = cells[r_idx][theta][6] if len(cells[r_idx][theta]) > 6 else (cells[r_idx][theta][3] if isinstance(cells[r_idx][theta][3], int) else -1)
                                                    if len(cells[r_idx][theta]) > 6:
                                                        cells[r_idx][theta][6] = (current_idx + 1) % num_wall_meshes
                                                    else:
                                                        cells[r_idx][theta][3] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = "outermost outward wall"
                                            
                                if modified:
                                    data_dict['cells'] = cells
                                    col["fire_maze_data"] = json.dumps(data_dict)
                                    rebuild_maze_from_collection(context, col)
                                    self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at cell ({r_idx}, {theta})")
                            else:
                                # Rectangular grid mesh swapping
                                cx = math.floor(hit_x / ts)
                                cy = math.floor(hit_y / ts)
                                if -1 <= cx <= width and -1 <= cy <= depth:
                                    # Determine clicked face direction
                                    face_dir = 'WALL'
                                    if normal:
                                        nx, ny, nz = abs(normal.x), abs(normal.y), abs(normal.z)
                                        if nz > nx and nz > ny:
                                            tiled = props.wall_height_tiled
                                            tiles_high = props.wall_height_tiles if tiled else 1
                                            wh = ts * tiles_high if tiled else props.wall_height
                                            face_dir = 'ROOF' if loc.z > (wh * 0.5) else 'FLOOR'
                                        elif ny > nx and ny > nz:
                                            face_dir = 'N' if normal.y > 0 else 'S'
                                        else:
                                            face_dir = 'E' if normal.x > 0 else 'W'
    
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
                                                if num_roof_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 6:
                                                    current_idx = cells[cy_clamped][cx_clamped][6] if isinstance(cells[cy_clamped][cx_clamped][6], int) else -1
                                                    cells[cy_clamped][cx_clamped][6] = (current_idx + 1) % num_roof_meshes
                                                    modified = True
                                                    rebuilt_text = "roof"
                                        elif face_dir == 'FLOOR':
                                            if num_floor_meshes > 0 and len(cells[cy_clamped][cx_clamped]) > 5:
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
                                                    idx_map = {'N': 1, 'S': 2, 'E': 3, 'W': 4}
                                                    face_idx = idx_map.get(face_dir, 1)
                                                    current_idx = cells[cy_clamped][cx_clamped][face_idx] if isinstance(cells[cy_clamped][cx_clamped][face_idx], int) else -1
                                                    cells[cy_clamped][cx_clamped][face_idx] = (current_idx + 1) % num_wall_meshes
                                                    modified = True
                                                    rebuilt_text = f"wall ({face_dir} face)"
                                                    
                                        if modified:
                                            data_dict['cells'] = cells
                                            col["fire_maze_data"] = json.dumps(data_dict)
                                            rebuild_maze_from_collection(context, col)
                                            self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at ({cx_clamped}, {cy_clamped})")
                                        else:
                                            if not is_wall:
                                                if num_floor_meshes > 0:
                                                    current_idx = cells[cy_clamped][cx_clamped][5] if isinstance(cells[cy_clamped][cx_clamped][5], int) else -1
                                                    cells[cy_clamped][cx_clamped][5] = (current_idx + 1) % num_floor_meshes
                                                    data_dict['cells'] = cells
                                                    col["fire_maze_data"] = json.dumps(data_dict)
                                                    rebuild_maze_from_collection(context, col)
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
                                                        modified = True
                                                        rebuilt_text = "horizontal wall"
                                                    else:
                                                        target_x = cx_clamped + 1 if min_d == d_E else cx_clamped
                                                        target_x = min(target_x, width - 1)
                                                        current_idx = cells[cy_clamped][target_x][5] if isinstance(cells[cy_clamped][target_x][5], int) else -1
                                                        cells[cy_clamped][target_x][5] = (current_idx + 1) % num_wall_meshes
                                                        modified = True
                                                        rebuilt_text = "vertical wall"
                                                
                                        if modified:
                                            data_dict['cells'] = cells
                                            col["fire_maze_data"] = json.dumps(data_dict)
                                            rebuild_maze_from_collection(context, col)
                                            self.report({'INFO'}, f"Swapped {rebuilt_text} mesh at ({cx_clamped}, {cy_clamped})")
                        else:
                            if grid_type == 'polar':
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
                                
                                modified = False
                                rebuilt_text = ""
                                
                                d_in = abs(r_hit - (r_idx - 0.5) * ts)
                                d_out = abs(r_hit - (r_idx + 0.5) * ts)
                                
                                def angle_diff(a, b):
                                    diff = (a - b) % (2 * math.pi)
                                    if diff > math.pi:
                                        diff -= 2 * math.pi
                                    return abs(diff)
                                    
                                d_cw = r_hit * angle_diff(phi_hit, (theta + 1) * alpha_r)
                                d_ccw = r_hit * angle_diff(phi_hit, theta * alpha_r)
                                
                                if r_idx == 0:
                                    min_d = d_out
                                else:
                                    min_d = min(d_in, d_out, d_cw, d_ccw)
                                
                                if wall_mode == 'cube':
                                    target_cell = (r_idx, theta)
                                    if face_dir == 'WALL':
                                        if min_d == d_cw:
                                            A = (r_idx, theta)
                                            B = (r_idx, (theta + 1) % Nr)
                                            if cells[A[0]][A[1]][0]:
                                                target_cell = A
                                            elif cells[B[0]][B[1]][0]:
                                                target_cell = B
                                        elif min_d == d_ccw:
                                            A = (r_idx, theta)
                                            B = (r_idx, (theta - 1) % Nr)
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
                                        elif min_d == d_out:
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
                                                    
                                    tr, ttheta = target_cell
                                    cells[tr][ttheta][0] = not cells[tr][ttheta][0]
                                    roof_pos = 5 if len(cells[tr][ttheta]) >= 8 else 5
                                    floor_pos = 4 if len(cells[tr][ttheta]) >= 8 else 4
                                    if cells[tr][ttheta][0]:
                                        if num_wall_meshes > 0:
                                            cells[tr][ttheta][2] = random.randrange(num_wall_meshes)
                                            cells[tr][ttheta][3] = random.randrange(num_wall_meshes)
                                            if len(cells[tr][ttheta]) > 7:
                                                cells[tr][ttheta][7] = random.randrange(num_wall_meshes)
                                            if len(cells[tr][ttheta]) > 8:
                                                cells[tr][ttheta][8] = random.randrange(num_wall_meshes)
                                        if num_roof_meshes > 0:
                                            cells[tr][ttheta][roof_pos] = random.randrange(num_roof_meshes)
                                    else:
                                        if num_floor_meshes > 0:
                                            cells[tr][ttheta][floor_pos] = random.randrange(num_floor_meshes)
                                    modified = True
                                    rebuilt_text = "wall tile"
                                    r_idx, theta = tr, ttheta
                                else:
                                    # Thin wall mode
                                    if min_d == d_cw:
                                        cells[r_idx][theta][0] = not cells[r_idx][theta][0]
                                        modified = True
                                        rebuilt_text = "clockwise wall"
                                    elif min_d == d_ccw:
                                        prev_theta = (theta - 1) % Nr
                                        cells[r_idx][prev_theta][0] = not cells[r_idx][prev_theta][0]
                                        modified = True
                                        rebuilt_text = "counter-clockwise wall"
                                    elif min_d == d_in:
                                        cells[r_idx][theta][1] = not cells[r_idx][theta][1]
                                        modified = True
                                        rebuilt_text = "inward wall"
                                    elif min_d == d_out:
                                        if r_idx + 1 < rings:
                                            N_out = ring_sectors[r_idx + 1]
                                            theta_out = math.floor(phi_hit / (2 * math.pi / N_out))
                                            theta_out = max(0, min(theta_out, N_out - 1))
                                            cells[r_idx + 1][theta_out][1] = not cells[r_idx + 1][theta_out][1]
                                            modified = True
                                            rebuilt_text = "outward wall"
                                            
                                if modified:
                                    data_dict['cells'] = cells
                                    col["fire_maze_data"] = json.dumps(data_dict)
                                    rebuild_maze_from_collection(context, col)
                                    self.report({'INFO'}, f"Toggled {rebuilt_text} at cell ({r_idx}, {theta})")
                            else:
                                # Rectangular grid toggle logic
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
                                        cells[ty][tx][0] = not cells[ty][tx][0]
                                        if cells[ty][tx][0]:
                                            if num_wall_meshes > 0:
                                                cells[ty][tx][1] = random.randrange(num_wall_meshes)
                                                cells[ty][tx][2] = random.randrange(num_wall_meshes)
                                                cells[ty][tx][3] = random.randrange(num_wall_meshes)
                                                cells[ty][tx][4] = random.randrange(num_wall_meshes)
                                            if num_roof_meshes > 0:
                                                cells[ty][tx][6] = random.randrange(num_roof_meshes)
                                        else:
                                            if num_floor_meshes > 0:
                                                cells[ty][tx][5] = random.randrange(num_floor_meshes)
                                        data_dict['cells'] = cells
                                        col["fire_maze_data"] = json.dumps(data_dict)
                                        rebuild_maze_from_collection(context, col)
                                        self.report({'INFO'}, f"Toggled wall tile at ({tx}, {ty})")
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
                                                cells[cy_clamped - 1][cx_clamped][2] = cells[cy_clamped][cx_clamped][3]
                                                
                                        data_dict['cells'] = cells
                                        col["fire_maze_data"] = json.dumps(data_dict)
                                        rebuild_maze_from_collection(context, col)
                                        self.report({'INFO'}, f"Toggled wall at cell ({cx_clamped}, {cy_clamped})")
            return {'RUNNING_MODAL'}


        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        props = context.scene.fire_maze
        
        # If already editing, act as a toggle off
        if props.is_editing:
            props.is_editing = False
            context.workspace.status_text_set(None)
            delete_edit_helper()
            col = None
            for c in bpy.data.collections:
                if "fire_maze_data" in c:
                    col = c
                    break
            if col:
                rebuild_maze_from_collection(context, col)
            self.report({'INFO'}, "Interactive Edit finished")
            return {'FINISHED'}

        col = None
        for c in bpy.data.collections:
            if "fire_maze_data" in c:
                col = c
                break
                
        if not col:
            self.report({'ERROR'}, "No generated maze found to edit. Please generate a maze first.")
            return {'CANCELLED'}
            
        props.is_editing = True
        rebuild_maze_from_collection(context, col)
        context.workspace.status_text_set("FireMaze Editor: Left-click walls to toggle. Shift+Left-click to cycle mesh. Enter/Esc to exit.")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


def _generate_maze_image_datablock(context, img_name="FireMaze_Layout"):
    col = bpy.data.collections.get("FireMaze")
    if not col or "fire_maze_data" not in col:
        return None, "No active maze layout found. Please generate a maze first."

    import json
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

    if wall_mode == 'cube':
        for y in range(depth):
            for x in range(width):
                is_wall = cells[y][x][0]
                val = 0.0 if is_wall else 1.0
                idx = (y * img_w + x) * 4
                pixels[idx] = val
                pixels[idx + 1] = val
                pixels[idx + 2] = val
                pixels[idx + 3] = 1.0
    else: # thin mode
        for y in range(depth):
            for x in range(width):
                c = cells[y][x]
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
    img.pixels = pixels
    img.update()
    return img, None


class MAZE_OT_save_as_image(bpy.types.Operator):
    bl_idname = "fire_maze.save_as_image"
    bl_label = "Save Maze as Image"
    bl_description = "Export the current maze layout as a black-and-white image in the Blender database"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        img, err = _generate_maze_image_datablock(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved maze layout as image '{img.name}' (Size: {img.size[0]}x{img.size[1]})")
        return {'FINISHED'}


class MAZE_OT_save_image_file(bpy.types.Operator, ExportHelper):
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
            except:
                pass
            self.report({'ERROR'}, f"Failed to save image file: {e}")
            return {'CANCELLED'}


class MAZE_OT_load_mask_image(bpy.types.Operator, ImportHelper):
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
        import os
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
    bl_idname = "fire_maze.restore_autosave"
    bl_label = "Restore Last Session"
    bl_description = "Restore maze settings and layout from the last session's autosave"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global show_recovery_warning
        show_recovery_warning = False
        import os
        import tempfile
        import json
        
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        if not os.path.exists(autosave_path):
            self.report({'ERROR'}, "No autosave file found")
            return {'CANCELLED'}
            
        try:
            with open(autosave_path, 'r') as f:
                data = json.load(f)
                
            _deserialize_session_data(context, data)
            
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
    bl_idname = "fire_maze.discard_autosave"
    bl_label = "Discard Recovery Data"
    bl_description = "Delete the temporary autosave recovery file from disk"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global show_recovery_warning
        show_recovery_warning = False
        import os
        import tempfile
        autosave_path = os.path.join(tempfile.gettempdir(), "firemaze_autosave.json")
        if os.path.exists(autosave_path):
            try:
                os.remove(autosave_path)
                self.report({'INFO'}, "Autosave recovery file discarded successfully")
            except Exception as e:
                self.report({'ERROR'}, f"Failed to delete recovery file: {e}")
                return {'CANCELLED'}
        else:
            self.report({'INFO'}, "No recovery file found to discard")
        return {'FINISHED'}

class MAZE_OT_save_session(bpy.types.Operator, ExportHelper):
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
        import json
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
        global show_recovery_warning
        show_recovery_warning = False
        import json
        import os
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
            _deserialize_session_data(context, data)
            self.report({'INFO'}, f"Session loaded successfully from: {self.filepath}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load session: {e}")
            return {'CANCELLED'}

classes = (MAZE_OT_generate, MAZE_OT_clear, MAZE_OT_interactive_edit, MAZE_OT_save_as_image, MAZE_OT_save_image_file, MAZE_OT_load_mask_image, MAZE_OT_restore_autosave, MAZE_OT_discard_autosave, MAZE_OT_save_session, MAZE_OT_load_session)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
