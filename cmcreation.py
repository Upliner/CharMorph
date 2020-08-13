# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 3
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####
#
# Copyright (C) 2020 Michael Vigovsky

import logging
import bpy, mathutils

logger = logging.getLogger(__name__)

class VIEW3D_PT_CMCreation(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_CMCreation"
    bl_label = "Character creation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context):
        pass

class CMCREATION_PT_Rigging(bpy.types.Panel):
    bl_label = "Rigging"
    bl_parent_id = "VIEW3D_PT_CMCreation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "CharMorph"

    def draw(self, context):
        ui = context.scene.cmcreation_ui
        self.layout.prop(ui, "rig_char")
        #self.layout.prop(ui, "rig_armature")
        self.layout.prop(ui, "rig_heads")
        self.layout.prop(ui, "rig_tails")
        self.layout.operator("cmcreation.joints_to_vg")
        self.layout.prop(ui, "rig_vg_calc")
        self.layout.prop(ui, "rig_vg_offs")
        if ui.rig_vg_calc == "NP":
            self.layout.prop(ui, "rig_vg_calc_n")
        elif ui.rig_vg_calc == "NR":
            self.layout.prop(ui, "rig_vg_calc_radius")

        self.layout.operator("cmcreation.calc_vg")

def obj_by_type(name, type):
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj and obj.type == type:
        return obj
def get_char():
    return obj_by_type(bpy.context.scene.cmcreation_ui.rig_char, "MESH")
def get_rig():
    return obj_by_type(bpy.context.scene.cmcreation_ui.rig_armature, "ARMATURE")

def get_vg_data(char, new, accumulate):
    data = {}
    for vid, v in enumerate(char.data.vertices):
        for gw in v.groups:
            vg = char.vertex_groups[gw.group]
            if not vg.name.startswith("joint_"):
                continue
            data_item = data.get(vg.name)
            if not data_item:
                data_item = new()
                data[vg.name] = data_item
            accumulate(data_item, vid, v, gw)
    return data

def get_vg_avg(char):
    def accumulate(data_item, vid, v, gw):
        data_item[0] += gw.weight
        data_item[1] += v.co*gw.weight
    return get_vg_data(char, lambda: [0, mathutils.Vector()], accumulate)

def joint_list():
    joints = []
    ui = bpy.context.scene.cmcreation_ui
    for bone in bpy.context.selected_editable_bones:
        if ui.rig_heads:
            joints.append(("joint_"+bone.name+"_head", bone.head, bone, "head"))
        if ui.rig_tails:
            joints.append(("joint_"+bone.name+"_tail", bone.tail, bone, "tail"))
    return joints

def joints_to_vg(char):
    avg = get_vg_avg(char)
    for name, _, bone, attr in joint_list():
        item = avg.get(name)
        if item:
            setattr(bone, attr, item[1]/item[0])
        else:
            logger.error("No vg for joint" + name)

def editable_bones_poll(context):
    return context.selected_editable_bones and len(context.selected_editable_bones) > 0 and get_char()

class OpJointsToVG(bpy.types.Operator):
    bl_idname = "cmcreation.joints_to_vg"
    bl_label = "Selected bones to VG"
    bl_description = "Move selected joints according to their vertex groups"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context):
        joints_to_vg(get_char())
        return {"FINISHED"}

def kdtree_from_obj(obj):
    verts = obj.data.vertices
    kd = mathutils.kdtree.KDTree(len(verts))
    for idx, vert in enumerate(verts):
        kd.insert(vert.co, idx)
    kd.balance()
    return kd

def recalc_cu(vg, lst, co):
    if len(lst) == 0:
        logger.error("No points")
        return False
    weights = mathutils.interpolate.poly_3d_calc([item[0] for item in lst], co)
    coeff = max(weights)
    if coeff < 1e-30:
        logger.error("Bad coeff")
        return False
    coeff = 1/coeff
    for weight, item in zip(weights, lst):
        vg.add([item[1]], weight*coeff, 'REPLACE')
    return True

def recalc_lst(char, co, name, lst):
    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    vg.add([item[1] for item in lst], 1, 'REPLACE')
    return recalc_cu(vg, lst, co)

def recalc_nf(char, co, name, bvh):
    _, _, idx, _ = bvh.find_nearest(co)
    if idx == None:
        logger.error("Face not found")
        return False
    verts = char.data.vertices
    return recalc_lst(char, co, name, [(verts[i].co, i) for i in char.data.polygons[idx].vertices])

def closest_point_on_face(face, co):
    if len(face)==3:
        return mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2])
    results = []
    for _ in range(len(face)):
        results.append(mathutils.geometry.closest_point_on_tri(co, face[0], face[1], face[2]))
        face=face[1:]+face[:1]
    results.sort(key=lambda elem: (elem-co).length)
    return (results[0]+results[1])/2

def recalc_bb(char, co, name):
    lst = [(None,None,None) for _ in range(8)]
    for idx, vert in enumerate(char.data.vertices):
        vco = vert.co
        for bv in range(8):
            for coord in range(3):
                if bv>>coord&1 != (1 if vco[coord]>co[coord] else 0):
                    break
            else:
                dist = sum(abs(vco[coord]-co[coord]) for coord in range(3))
                if lst[bv][2]==None or lst[bv][2]>dist:
                    lst[bv] = (vco, idx, dist)
                break
    for item in lst:
        if item[0] == None:
            logger.error("Not all bbox points was found")
            return False

    front_face = [item[0] for item in lst[:4]]
    back_face = [item[0] for item in lst[4:]]

    front_face[2],front_face[3] = front_face[3],front_face[2]
    back_face[2],back_face[3] = back_face[3],back_face[2]

    weights_front = mathutils.interpolate.poly_3d_calc(front_face, closest_point_on_face(front_face, co))
    weights_back = mathutils.interpolate.poly_3d_calc(back_face, closest_point_on_face(back_face, co))

    avg_front = sum((co*weight for co, weight in zip(front_face,weights_front)), mathutils.Vector())
    avg_back = sum((co*weight for co, weight in zip(back_face,weights_back)), mathutils.Vector())
    axis = avg_back-avg_front
    offs = min(max((co-avg_front).dot(axis)/axis.dot(axis),0),1)

    weights_front[2],weights_front[3] = weights_front[3],weights_front[2]
    weights_back[2],weights_back[3] = weights_back[3],weights_back[2]

    weights = [w*(1-offs) for w in weights_front]+[w*offs for w in weights_back]

    if name in char.vertex_groups:
        char.vertex_groups.remove(char.vertex_groups[name])
    vg = char.vertex_groups.new(name=name)
    for item, weight in zip(lst, weights):
        vg.add([item[1]], weight, 'REPLACE')

def recalc_np(char, co, name, kd):
    return recalc_lst(char, co, name, kd.find_n(co, bpy.context.scene.cmcreation_ui.rig_vg_n))
def recalc_nr(char, co, name, kd):
    return recalc_lst(char, co, name, kd.find_range(co, bpy.context.scene.cmcreation_ui.rig_vg_radius))

class OpCalcVg(bpy.types.Operator):
    bl_idname = "cmcreation.calc_vg"
    bl_label = "Recalc vertex groups"
    bl_description = "Recalculate joint vertex groups according to baricentric coordinates of 3 nearest points of bone positions"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return editable_bones_poll(context)

    def execute(self, context):
        char = get_char()
        ui = context.scene.cmcreation_ui
        typ = ui.rig_vg_calc

        joints = joint_list()

        if typ == "CU":
            vgroups = get_vg_data(char, lambda: [], lambda data_item, vid, v, gw: data_item.add((v.co, vid)))
            for name, co, _, _ in joints:
                vg = char.vertex_groups.get(name)
                if not vg:
                    logger.error(name + " doesn't have current vertex group")
                    continue
                recalc_cu(vg, vgroups.get(name,[]), co)
        else:
            if typ == "NP" or typ == "NR":
                kd = kdtree_from_obj(char)
            elif typ == "NF":
                bvh = mathutils.bvhtree.BVHTree.FromPolygons([v.co for v in char.data.vertices], [f.vertices for f in char.data.polygons])

            for name, co, _, _ in joints:
                if typ == "NP":
                    recalc_np(char, co, name, kd)
                elif typ == "NR":
                    recalc_nr(char, co, name, kd)
                elif typ == "NF":
                    recalc_nf(char, co, name, bvh)
                elif typ == "BB":
                    recalc_bb(char, co, name)
                else:
                    logger.error("Inavlid typ!")

        if ui.rig_vg_offs == "R":
            avg = get_vg_avg(char)
            for name, co, bone, attr in joints:
                item = avg.get(name)
                if item:
                    offs = co-(item[1]/item[0])
                    k = "charmorph_offs_"+attr
                    if offs.length>0.0001:
                        bone[k] = list(offs)
                    elif k in bone:
                        del bone[k]
                else:
                    logger.error("Can't calculate offset for " + name)
        elif ui.rig_vg_offs == "C":
            for _, _, bone, attr in joints:
                k = "charmorph_offs_"+attr
                if k in bone:
                    del bone[k]

        return {"FINISHED"}

def objects_by_type(type):
    return [(o.name,o.name,"") for o in bpy.data.objects if o.type == type]

class CMCreationUIProps(bpy.types.PropertyGroup):
    # Rigging
    rig_char: bpy.props.EnumProperty(
        name = "Char",
        items = lambda ui, context: objects_by_type("MESH"),
        description = "Character mesh for rigging"
    )
    rig_heads: bpy.props.BoolProperty(
        name = "Heads",
        description = "Affect bone heads for joints and vg manupulations",
        default=True)
    rig_tails: bpy.props.BoolProperty(
        name = "Tails",
        description = "Affect bone heads for joints and vg manupulations",
        default=True,
    )
    rig_vg_calc: bpy.props.EnumProperty(
        name = "Recalc mode",
        default="NF",
        items = [
            ("CU", "Current","Use current vertex group members and recalc only weights"),
            ("NP", "n nearest points","Recalculate vertex group based on n nearest points"),
            ("NR", "By distance","Recalculate vertex group based on vertices within specified distance"),
            ("NF", "Nearest face","Recalculate vertex group based on nearest face"),
            ("BB", "Bounding box (exp)","Recalculate vertex group based on smallest bounding box vertices (experimental)"),
        ]
    )
    rig_vg_offs: bpy.props.EnumProperty(
        name = "Offsets",
        description = "Use offset if vertex group can't properly point at joint position",
        default="C",
        items=[
            ("K","Keep","Keep current offsets"),
            ("R","Recalculate","Recalculate offsets exactly point specified joint position"),
            ("C","Clear","Clear any offsets, use only vertex group positions"),
        ]
    )
    rig_vg_n: bpy.props.IntProperty(
        name = "VG Point count",
        description = "Point count for vertex group recalc",
        default=3,
        min=1, soft_max=20,
    )
    rig_vg_radius: bpy.props.FloatProperty(
        name = "VG recalc radius",
        description = "Radius for vertex group recalc",
        default=0.1,
        min=0, soft_max=0.5,
    )
    rig_vg_n: bpy.props.IntProperty(
        name = "VG Point count",
        description = "Point count for vertex group recalc",
        default=3,
        min=1, soft_max=20,
    )

classes = [CMCreationUIProps, OpJointsToVG, OpCalcVg, VIEW3D_PT_CMCreation, CMCREATION_PT_Rigging]

register_classes, unregister_classes = bpy.utils.register_classes_factory(classes)

def register():
    register_classes()
    bpy.types.Scene.cmcreation_ui = bpy.props.PointerProperty(type=CMCreationUIProps, options={"SKIP_SAVE"})

def unregister():
    del bpy.types.Scene.cmcreation_ui
    unregister_classes()

if __name__ == "__main__":
    register()
