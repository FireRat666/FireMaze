"""Blender UI panel classes for the FireMaze addon sidebar.

Each panel is a child of ``VIEW3D_PT_fire_maze`` and organises
maze settings into collapsible sections.
"""

import bpy
from . import operators

class VIEW3D_PT_fire_maze(bpy.types.Panel):
    """Main FireMaze panel with grid settings, generation and editing controls."""

    bl_label = "FireMaze"
    bl_idname = "VIEW3D_PT_fire_maze"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"

    def draw(self, context):
        """Draw the main settings box: grid type, dimensions, wall mode, tile size and floors."""
        layout = self.layout
        props = context.scene.fire_maze

        box = layout.box()
        box.label(text="Maze Settings", icon='GRID')
        col = box.column(align=True)
        col.prop(props, "grid_type", text="Grid Type")
        col.separator(factor=0.5)
        if props.grid_type == 'rect':
            col.prop(props, "width")
            col.prop(props, "depth")
        else:
            col.prop(props, "polar_rings", text="Rings")
            col.prop(props, "polar_custom_alignment", text="Custom Tile Alignment")
        col.separator(factor=0.5)
        col.prop(props, "wall_height_tiled")
        if props.wall_height_tiled:
            col.prop(props, "wall_height_tiles")
        else:
            col.prop(props, "wall_height")
        col.row().prop(props, "wall_mode", expand=True)
        if props.wall_mode == 'thin':
            col.prop(props, "wall_thickness")
        col.prop(props, "tile_size")
        col.separator(factor=0.5)
        col.prop(props, "floors")
        if props.floors > 1:
            if props.grid_type == 'rect':
                col.prop(props, "stair_footprint")
            col.prop(props, "stair_style")
            col.prop(props, "stair_direction")
            col.prop(props, "stair_count")
            if props.mask_image:
                warn = col.box()
                warn.alert = True
                warn.label(text="Mask disabled when floors > 1", icon='ERROR')

        box = layout.box()
        box.label(text="Generation & Editing", icon='FILE_REFRESH')
        col = box.column(align=True)
        col.prop(props, "seed")
        col.separator(factor=0.5)
        if props.floors > 1:
            col.prop(props, "edit_floor_level", text="Floor Level")
            col.separator(factor=0.5)

        if props.is_editing:
            col.operator("fire_maze.interactive_edit", text="Exit Edit Mode", icon="CANCEL", depress=True)
            if props.wall_mode == 'thin' and props.grid_type == 'polar':
                col.prop(props, "edit_roof", text="Edit Roof", icon="MESH_PLANE")
            if props.floors > 1:
                col.separator(factor=0.5)
                col.row().prop(props, "edit_tool", expand=True)
                if props.edit_tool == 'stair':
                    col.prop(props, "stair_direction")
            alert_box = layout.box()
            alert_box.alert = True
            alert_box.label(text="Editing Mode Active", icon="ERROR")
            if props.wall_mode == 'thin' and props.grid_type == 'polar' and props.edit_roof:
                alert_box.label(text="Shift-Click to swap/edit roof mesh")
            elif props.edit_tool == 'stair':
                alert_box.label(text="Left-Click to toggle stairs")
            else:
                alert_box.label(text="Left-Click walls to toggle")
            alert_box.label(text="Press Esc or click Exit to finish")
        else:
            col.operator("fire_maze.interactive_edit", text="Interactive Edit", icon="EDITMODE_HLT")

        layout.separator(factor=0.5)

        row = layout.row(align=True)
        row.scale_y = 1.8
        row.operator("fire_maze.generate", text="Generate Maze", icon='MESH_GRID')
        layout.separator(factor=0.5)
        layout.operator("fire_maze.clear", text="Clear Maze", icon='TRASH')


class VIEW3D_PT_fire_maze_algorithm(bpy.types.Panel):
    """Algorithm selection and optional room generation settings."""

    bl_label = "Algorithm & Rooms"
    bl_idname = "VIEW3D_PT_fire_maze_algorithm"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw the algorithm selector and optional room settings."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "algorithm")
        col.separator(factor=0.5)
        
        # Draw algorithm-specific parameters
        if props.algorithm == 'growing_tree':
            col.prop(props, "selection_bias", slider=True)
        elif props.algorithm in ('dfs', 'hunt_and_kill'):
            col.prop(props, "straightness", slider=True)
            if props.grid_type == 'polar' and props.algorithm == 'dfs':
                col.prop(props, "radial_bias", slider=True)
        elif props.algorithm == 'binary_tree':
            col.prop(props, "direction_bias", slider=True)
        elif props.algorithm == 'sidewinder':
            col.prop(props, "east_bias", slider=True)
        elif props.algorithm == 'recursive_division':
            col.prop(props, "orientation_bias", slider=True)
            col.prop(props, "passage_bias", slider=True)
        elif props.algorithm == 'eller':
            col.prop(props, "eller_merge_prob", slider=True)
        
        if props.grid_type == 'rect':
            col.separator(factor=0.5)
            col.prop(props, "rooms_enable")
            if props.rooms_enable:
                col.prop(props, "rooms_count")
                col.prop(props, "min_room_size")
                col.prop(props, "max_room_size")
                


class VIEW3D_PT_fire_maze_session(bpy.types.Panel):
    """Session save/load, autosave recovery, mask image and image export."""

    bl_label = "Session & Image Management"
    bl_idname = "VIEW3D_PT_fire_maze_session"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw session save/load, autosave recovery, mask image and image export UI."""
        layout = self.layout
        props = context.scene.fire_maze

        # 1. Session Files Box
        box = layout.box()
        box.label(text="Session Management", icon='FILE_BLEND')
        col = box.column(align=True)
        row = col.row(align=True)
        row.operator("fire_maze.save_session", text="Save Session...", icon='EXPORT')
        row.operator("fire_maze.load_session", text="Load Session...", icon='IMPORT')

        if getattr(operators, "has_autosave", False) and getattr(operators, "show_recovery_warning", True):
            col.separator(factor=0.5)
            rec_box = col.box()
            rec_box.alert = True
            rec_box.label(text="Unsaved session recovery available", icon='INFO')
            row_rec = rec_box.row(align=True)
            row_rec.operator("fire_maze.restore_autosave", text="Restore Session", icon='RECOVER_LAST')
            row_rec.operator("fire_maze.discard_autosave", text="Discard", icon='TRASH')

        # 2. Masking & Image Export Box
        box2 = layout.box()
        box2.label(text="Masking & Image Export", icon='IMAGE_DATA')
        col2 = box2.column(align=True)
        if props.grid_type != 'rect':
            col2.label(text="Masking requires Rectangular grid", icon='INFO')
        else:
            col2.operator("fire_maze.load_mask_image", text="Load Mask from Disk...", icon='FILE_IMAGE')
            col2.prop(props, "mask_image", text="Selected Mask")
            if props.mask_image:
                col2.prop(props, "mask_invert", text="Invert Mask Colors")
        
        col2.separator(factor=0.5)
        col2.label(text="Export Layout:")
        row2 = col2.row(align=True)
        row2.operator("fire_maze.save_image_file", text="Save PNG to Disk...", icon='EXPORT')
        row2.operator("fire_maze.save_as_image", text="Create Blender Image", icon='IMAGE_ZDEPTH')


class VIEW3D_PT_fire_maze_entrances(bpy.types.Panel):
    """Entrance / exit side, count and maze mode (center or exit)."""

    bl_label = "Entrances & Exits"
    bl_idname = "VIEW3D_PT_fire_maze_entrances"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        """Show this panel only for rectangular grid mazes."""
        return context.scene.fire_maze.grid_type == 'rect'

    def draw(self, context):
        """Draw entrance/exit side, count and maze mode settings."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.row().prop(props, "mode", expand=True)
        col.separator(factor=0.3)
        col.prop(props, "entrance_side")
        col.prop(props, "num_entrances")
        col.separator(factor=0.3)
        col.prop(props, "exit_side")
        col.prop(props, "num_exits")
        if props.mode == 'center':
            col.separator(factor=0.3)
            col.prop(props, "emergency_exits")


class VIEW3D_PT_fire_maze_loops(bpy.types.Panel):
    """Loop probability and isolated wall placement controls."""

    bl_label = "Loops & Layout"
    bl_idname = "VIEW3D_PT_fire_maze_loops"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        """Show this panel only for rectangular grid mazes."""
        return context.scene.fire_maze.grid_type == 'rect'

    def draw(self, context):
        """Draw loop probability and isolated wall placement controls."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "loop_probability", slider=True)
        col.prop(props, "isolated_wall_prob", slider=True)


class VIEW3D_PT_fire_maze_guide(bpy.types.Panel):
    """Pathfinding guide path settings (curve, tube, ribbon with sine wave)."""

    bl_label = "Guide Path"
    bl_idname = "VIEW3D_PT_fire_maze_guide"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw guide path toggle and curve/tube/ribbon settings."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "generate_guide")
        if props.generate_guide:
            col.prop(props, "guide_type")
            col.prop(props, "guide_width")
            col.prop(props, "guide_height_offset")
            col.prop(props, "guide_wave_amplitude")
            col.prop(props, "guide_wave_frequency")


class VIEW3D_PT_fire_maze_custom_tiles(bpy.types.Panel):
    """Custom mesh and collection selectors for floors, walls, roofs, stairs."""

    bl_label = "Custom Meshes & Collections"
    bl_idname = "VIEW3D_PT_fire_maze_custom_tiles"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw custom mesh/collection selectors for floors, walls, roofs and stairs."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "tiles_centered")
        col.separator(factor=0.3)
        col.prop(props, "custom_floor_mesh", text="Floor Mesh")
        col.prop(props, "custom_floor_collection", text="Floor Collection")
        col.prop(props, "custom_roof_mesh", text="Roof Mesh")
        col.prop(props, "custom_roof_collection", text="Roof Collection")
        col.separator(factor=0.3)
        col.prop(props, "custom_stair_mesh", text="Stair Mesh")
        col.prop(props, "custom_ramp_mesh", text="Ramp Mesh")
        col.separator(factor=0.3)
        col.prop(props, "custom_wall_mesh", text="Wall Mesh")
        if props.wall_mode == 'thin' and props.grid_type == 'rect':
            col.prop(props, "thin_wall_double_sided", text="Double-Sided Thin Walls")
            col.prop(props, "clean_wall_corners", text="Clean Wall Corners")
        col.separator(factor=0.3)
        col.prop(props, "custom_wall_collection", text="Wall Collection")
        if props.wall_mode == 'cube':
            col.prop(props, "cube_mode_pillar", text="Pillar Mode (Cube)")


class VIEW3D_PT_fire_maze_transforms(bpy.types.Panel):
    """Per-element translate / rotate / scale offsets for walls, floors and roofs."""

    bl_label = "Detailed Transforms"
    bl_idname = "VIEW3D_PT_fire_maze_transforms"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw per-element translate/rotate/scale offsets for walls, floors and roofs."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.label(text="Wall Offsets:")
        col.prop(props, "wall_translate", text="Translate")
        col.prop(props, "wall_rotate", text="Rotate")
        col.prop(props, "wall_scale", text="Scale")
        col.separator(factor=0.5)
        col.label(text="Floor Offsets:")
        col.prop(props, "floor_translate", text="Translate")
        col.prop(props, "floor_rotate", text="Rotate")
        col.prop(props, "floor_scale", text="Scale")
        col.separator(factor=0.5)
        col.label(text="Roof Offsets:")
        col.prop(props, "roof_translate", text="Translate")
        col.prop(props, "roof_rotate", text="Rotate")
        col.prop(props, "roof_scale", text="Scale")



class VIEW3D_PT_fire_maze_cleanup(bpy.types.Panel):
    """Post-processing options: merge, optimize, vertex paint, lightmap, colliders."""

    bl_label = "Post-Processing"
    bl_idname = "VIEW3D_PT_fire_maze_cleanup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw merge, optimise, vertex paint, lightmap and collider post-processing options."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "single_wall_object")
        col.prop(props, "merge_objects")
        col.prop(props, "remove_doubles")
        col.prop(props, "generate_lightmap")
        if props.generate_lightmap:
            col.prop(props, "lightmap_method", text="Method")
        col.prop(props, "optimize_coplanar")
        if props.optimize_coplanar:
            warn_box = layout.box()
            warn_box.alert = True
            warn_box.label(text="Warning: Planar dissolve simplifies geometry", icon='ERROR')
            warn_box.label(text="but can stretch/break seamless tiled textures.")
        
        col.separator()
        col.prop(props, "vertex_paint_enable")
        if props.vertex_paint_enable:
            col.prop(props, "vertex_paint_mode", text="Mode")
            col.prop(props, "vertex_paint_intensity", text="Intensity", slider=True)

        col.separator()
        col.prop(props, "generate_colliders")
        if props.generate_colliders:
            col.prop(props, "merge_colliders")
            col.prop(props, "optimize_colliders_coplanar")



class VIEW3D_PT_fire_maze_props(bpy.types.Panel):
    """Experimental prop and decor spawner (torches, chests, doors)."""

    bl_label = "Prop & Decor Spawner EXPERIMENTAL"
    bl_idname = "VIEW3D_PT_fire_maze_props"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FireRat"
    bl_parent_id = "VIEW3D_PT_fire_maze"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        """Draw prop spawner controls for torches, chests and doors."""
        layout = self.layout
        props = context.scene.fire_maze
        col = layout.column(align=True)
        col.prop(props, "prop_torch_mesh", text="Torch Object")
        if props.prop_torch_mesh:
            col.prop(props, "prop_torch_density", text="Torch Density", slider=True)
        col.separator()
        col.prop(props, "prop_chest_mesh", text="Chest Object")
        if props.prop_chest_mesh:
            col.prop(props, "prop_chest_density", text="Chest Density", slider=True)
        col.separator()
        col.prop(props, "prop_door_mesh", text="Door Object")


classes = (
    VIEW3D_PT_fire_maze,
    VIEW3D_PT_fire_maze_algorithm,
    VIEW3D_PT_fire_maze_session,
    VIEW3D_PT_fire_maze_entrances,
    VIEW3D_PT_fire_maze_loops,
    VIEW3D_PT_fire_maze_guide,
    VIEW3D_PT_fire_maze_custom_tiles,
    VIEW3D_PT_fire_maze_transforms,
    VIEW3D_PT_fire_maze_cleanup,
    VIEW3D_PT_fire_maze_props,
)

def register():
    """Register all FireMaze UI panel classes."""
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    """Unregister all FireMaze UI panel classes."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
