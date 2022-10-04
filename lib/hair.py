
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
# Copyright (C) 2021-2022 Michael Vigovsky
import logging, numpy

import bpy  # pylint: disable=import-error

from . import fit_calc, utils

logger = logging.getLogger(__name__)


def np_particles_data(obj, particles, precision=numpy.float32):
    cnt = numpy.empty(len(particles), dtype=numpy.uint8)
    total = 0
    mx = 1
    for i, p in enumerate(particles):
        c = len(p.hair_keys)
        cnt[i] = c
        total += c
        if c > mx:
            mx = c

    data = numpy.empty((total, 3), dtype=precision)
    tmp = numpy.empty(mx * 3 + 3, dtype=precision)
    i = 0
    for p in particles:
        t2 = tmp[:len(p.hair_keys) * 3]
        p.hair_keys.foreach_get("co_local", t2)
        t2 = t2.reshape((-1, 3))
        data[i:i + len(t2)] = t2
        i += len(t2)

    utils.np_matrix_transform(data, obj.matrix_world.inverted_safe())
    return {"cnt": cnt, "data": data, "config": "{\"version\":1}"}


def export_particles(obj, psys_idx, filepath, precision):
    pss = obj.particle_systems
    old_psys_idx = pss.active_index
    pss.active_index = psys_idx

    psys = pss[psys_idx]
    is_global = psys.is_global_hair
    override = {"object": obj}
    if not is_global:
        bpy.ops.particle.disconnect_hair(override)
    numpy.savez_compressed(filepath, **np_particles_data(obj, psys.particles, precision))
    if not is_global:
        bpy.ops.particle.connect_hair(override)

    pss.active_index = old_psys_idx


def update_particles(obj, cnts, morphed):
    t = utils.Timer()
    psys = obj.particle_systems.active
    psys.use_hair_dynamics = False
    eobj = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    epsys = eobj.particle_systems.active
    for mod in eobj.modifiers:
        if mod.type == "PARTICLE_SYSTEM" and mod.particle_system == epsys:
            break
    else:
        logger.error("Particle system modifier is not found for %s %s", obj.name, psys.name)
        return False

    t.time("eval")

    pos = 0
    for p, cnt in zip(psys.particles, cnts):
        if len(p.hair_keys) != cnt + 1:
            logger.error("Particle mismatch %d %d", len(p.hair_keys), cnt)
            return False
        for k in p.hair_keys[1:]:
            k.co_object_set(eobj, mod, p, morphed[pos])
            pos += 1
    t.time("hair_set")
    return True


class HairData:
    __slots__ = "cnts", "data", "binding"
    cnts: numpy.ndarray
    data: numpy.ndarray
    binding: fit_calc.FitBinding

    def get_morphed(self, diff: numpy.ndarray):
        result = self.binding.fit(diff)
        result += self.data
        return result


class HairFitter(fit_calc.MorpherFitCalculator):
    def __init__(self, *args):
        super().__init__(*args)
        self.hair_cache = {}

    def get_diff_arr(self):
        return self.mcore.get_diff()

    def _get_hair_data(self, target, props, hair_type):
        hd = HairData()
        if hair_type == "c":
            attr = target.attributes.get("charmorph_basis")
            if attr:
                hd.data = numpy.empty(len(attr.data) * 3, numpy.float64)
                attr.data.foreach_get("vector", hd.data)
                hd.data = hd.data.reshape(-1, 3)
                return hd

        z = self.mcore.char.get_np(f"hairstyles/{props.get('charmorph_hairstyle','')}.npz")
        if z is None:
            logger.error("Hairstyle npz file is not found")
            return None

        hd.cnts = z["cnt"]
        hd.data = z["data"].astype(dtype=numpy.float64, casting="same_kind")

        if hair_type == "p" and "config" in z:
            hd.data = numpy.delete(
                hd.data,
                numpy.concatenate((numpy.array((0,), dtype=hd.cnts.dtype), hd.cnts.cumsum()[:-1])),
                axis=0
            )
            hd.cnts -= 1

        if len(hd.cnts) != (len(target.particles) if hair_type == "p" else len(target.curves)):
            logger.error("Mismatch between current hairsyle and .npz!")
            return None

        return hd

    def get_hair_data(self, target):
        if isinstance(target, bpy.types.ParticleSystem):
            if not target.is_edited:
                return None
            hair_type = "p"
            props = target.settings
        else:
            hair_type = "c"
            props = target

        fit_id = props.get("charmorph_fit_id")
        if fit_id:
            data = self.hair_cache.get(hair_type + fit_id)
            if isinstance(data, HairData):
                return data

        hd = self._get_hair_data(target, props, hair_type)
        if not hd:
            return None

        binder = str(props.get("charmorph_binder_fit", "")).upper() or\
            bpy.context.window_manager.charmorph_ui.fitting_binder_weights

        hd.binding = self.calc_binding_hair(hd.data, binder)
        self.hair_cache[hair_type + fit_id] = hd
        return hd

    # use separate get_diff function to support hair fitting for posed characters
    def get_diff_hair(self):
        char = self.mcore.obj
        if not char.find_armature():
            return self.get_diff_arr()

        restore_modifiers = utils.disable_modifiers(char)
        echar = char.evaluated_get(bpy.context.evaluated_depsgraph_get())
        try:
            deformed = echar.to_mesh()
            basis = self.mcore.get_basis_alt_topo()
            if len(deformed.vertices) != len(basis):
                logger.error("Can't fit posed hair: vertex count mismatch")
                return self.get_diff_arr()
            result = numpy.empty(len(basis) * 3)
            deformed.vertices.foreach_get("co", result)
            result = result.reshape(-1, 3)
            result -= basis
            return result
        finally:
            echar.to_mesh_clear()
            for m in restore_modifiers:
                m.show_viewport = True

    def fit_particles(self, obj, idx):
        t = utils.Timer()
        psys = obj.particle_systems[idx]
        hd = self.get_hair_data(psys)
        if not hd:
            return False

        obj.particle_systems.active_index = idx

        restore_modifiers = utils.disable_modifiers(obj, lambda m: m.type == "SHRINKWRAP")
        try:
            update_particles(obj, hd.cnts, hd.get_morphed(self.get_diff_hair()))
        finally:
            for m in restore_modifiers:
                m.show_viewport = True

        t.time("p_hair_fit")
        return True

    def fit_obj_particles(self, obj):
        has_fit = False
        for i in range(len(obj.particle_systems)):
            has_fit |= self.fit_particles(obj, i)
        return has_fit

    def fit_curves(self, obj):
        t = utils.Timer()
        hd = self.get_hair_data(obj.data)
        if not hd:
            return False

        if obj.data.surface is None:
            diff = self.get_diff_hair()
        else:
            diff = self.get_diff_arr()

        obj.data.position_data.foreach_set("vector", hd.get_morphed(diff).reshape(-1))
        obj.update_tag()

        t.time("c_hair_fit")
        return True
