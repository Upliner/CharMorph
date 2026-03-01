# toonify.py

import bpy

def add_solidify_modifier_with_material(obj, material_name):
    # Check if the material exists
    material = bpy.data.materials.get(material_name)
    if not material:
        # Create a new material if it does not exist
        material = bpy.data.materials.new(name=material_name)
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        if bsdf:
            bsdf.inputs['Base Color'].default_value = (0, 0, 0, 1)  # Set color to black

    # Find the material index
    if material_name in obj.data.materials:
        material_index = obj.data.materials.find(material_name)
    else:
        obj.data.materials.append(material)
        material_index = len(obj.data.materials) - 1

    # Add the Solidify modifier
    solidify_mod = obj.modifiers.new(name="Solidify", type='SOLIDIFY')
    solidify_mod.thickness = -0.12
    solidify_mod.offset = 0.0
    solidify_mod.use_even_offset = True
    solidify_mod.material_offset = material_index
    solidify_mod.thickness_clamp = 0.4000

    return solidify_mod

def toonify_material(material):
    # Ensure the material uses nodes
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    
    # Find existing image texture node, if any
    image_texture = None
    for node in nodes:
        if node.type == 'TEX_IMAGE':
            image_texture = node
            break
    
    if not image_texture:
        return  # No image texture, skip this material

    # Clear existing nodes except the image texture
    for node in nodes:
        if node != image_texture:
            nodes.remove(node)


    # Add Principled BSDF
    principled = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled.location = (-300, 300)


    # Add Shader to RGB
    shader_to_rgb = nodes.new(type='ShaderNodeShaderToRGB')
    shader_to_rgb.location = (-100, 300)

    # Add color ramp
    color_ramp = nodes.new(type='ShaderNodeValToRGB')
    color_ramp.location = (100, 300)
    color_ramp.color_ramp.interpolation = 'CONSTANT'
    # Ensure there are exactly two elements and set their positions and colors
    color_ramp.color_ramp.elements.remove(color_ramp.color_ramp.elements[1])  # Remove default second element
    color_ramp.color_ramp.elements[0].position = 0.341  # First stop at the start
    color_ramp.color_ramp.elements[0].color = (0, 0, 0, 1)  # Black color
    second_element = color_ramp.color_ramp.elements.new(0.332)  # Add second stop
    second_element.color = (1, 1, 1, 1)  # White color

    # Add Emission nodes
    emission1 = nodes.new(type='ShaderNodeEmission')
    emission1.location = (300, 400)
    
    emission2 = nodes.new(type='ShaderNodeEmission')
    emission2.location = (300, 200)
    
    # Add Mix Shader
    mix_shader = nodes.new(type='ShaderNodeMixShader')
    mix_shader.location = (500, 300)
    
    # Add Material Output
    material_output = nodes.new(type='ShaderNodeOutputMaterial')
    material_output.location = (700, 300)

    # Add Hue/Saturation Value
    hsv = nodes.new(type='ShaderNodeHueSaturation')
    hsv.location = (-300, 0)
    
    # Link nodes
    links.new(image_texture.outputs['Color'], hsv.inputs['Color'])
    links.new(image_texture.outputs['Color'], emission1.inputs['Color'])
    links.new(hsv.outputs['Color'], emission2.inputs['Color'])
    links.new(principled.outputs['BSDF'], shader_to_rgb.inputs['Shader'])
    links.new(shader_to_rgb.outputs['Color'], color_ramp.inputs['Fac'])
    links.new(color_ramp.outputs['Color'], mix_shader.inputs['Fac'])
    links.new(mix_shader.outputs['Shader'], material_output.inputs['Surface'])
    links.new(emission1.outputs['Emission'], mix_shader.inputs[1])
    links.new(emission2.outputs['Emission'], mix_shader.inputs[2])
    
    # Set default values
    hsv.inputs['Saturation'].default_value = 1.0
    hsv.inputs['Value'].default_value = 0.2
    emission2.inputs['Strength'].default_value = 1.0

def apply_toonify():
    obj = bpy.context.active_object
    
    if bpy.context.selected_objects:
        add_solidify_modifier_with_material(obj, 'Line')
    else:
        print("No object selected.")
    if obj is not None:
        for material_slot in obj.material_slots:
            material = material_slot.material
            if material:
                toonify_material(material)
    else:
        print("No active object selected.")

class ToonifyOperator(bpy.types.Operator):
    bl_idname = "object.toonify_operator"
    bl_label = "Toonify"
    
    def execute(self, context):
        apply_toonify()
        return {'FINISHED'}



class ToonifyPanel(bpy.types.Panel):
    bl_label = "Toonify"
    bl_parent_id = "VIEW3D_PT_CharMorph"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 8

    def draw(self, context):
        layout = self.layout
        layout.operator("object.toonify_operator")

    toggle: bpy.props.BoolProperty(name="Expand", default=False) # type: ignore

classes = [
    ToonifyOperator, ToonifyPanel
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
