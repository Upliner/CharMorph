
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
        c = len(p.hair_keys) - 1
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
        t2 = t2[3:].reshape((-1, 3))
        data[i:i + len(t2)] = t2
        i += len(t2)

    utils.np_matrix_transform(data, obj.matrix_world.inverted())
    return {"cnt": cnt, "data": data}


def export_hair(obj, psys_idx, filepath, precision):
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


def update_hair(obj, cnts, morphed):
    t = utils.Timer()
    utils.np_matrix_transform(morphed[1:], obj.matrix_world)
    psys = obj.particle_systems.active
    have_mismatch = False
    t.time("hcalc")

    # I wish I could just get a transformation matrix for every particle and avoid these disconnects/connects!
    override = {"object": obj}
    bpy.ops.particle.disconnect_hair(override)
    t.time("disconnect")
    try:
        pos = 0
        for p, cnt in zip(psys.particles, cnts):
            if len(p.hair_keys) != cnt + 1:
                if not have_mismatch:
                    logger.error("Particle mismatch %d %d", len(p.hair_keys), cnt)
                    have_mismatch = True
                continue
            marr = morphed[pos:pos + cnt + 1]
            marr[0] = p.hair_keys[0].co_local
            pos += cnt
            p.hair_keys.foreach_set("co_local", marr.reshape(-1))
    finally:
        t.time("hair_set")
        bpy.ops.particle.connect_hair(override)
        t.time("connect")
    return True


class HairData:
    __slots__ = "cnts", "data", "binding"
    cnts: numpy.ndarray
    data: numpy.ndarray
    binding: fit_calc.FitBinding

    def get_morphed(self, diff: numpy.ndarray):
        result = numpy.empty((len(self.data) + 1, 3))
        result[1:] = self.binding.fit(diff)
        result[1:] += self.data
        return result


class HairFitter(fit_calc.MorpherFitCalculator):
    def __init__(self, *args):
        super().__init__(*args)
        self.hair_cache = {}

    def get_diff_arr(self):
        return self.mcore.get_diff()

    def get_hair_data(self, psys):
        if not psys.is_edited:
            return None
        fit_id = psys.settings.get("charmorph_fit_id")
        if fit_id:
            data = self.hair_cache.get(fit_id)
            if isinstance(data, HairData):
                return data

        z = self.mcore.char.get_np(f"hairstyles/{psys.settings.get('charmorph_hairstyle','')}.npz")
        if z is None:
            logger.error("Hairstyle npz file is not found")
            return None

        hd = HairData()
        hd.cnts = z["cnt"]
        hd.data = z["data"].astype(dtype=numpy.float64, casting="same_kind")

        if len(hd.cnts) != len(psys.particles):
            logger.error("Mismatch between current hairsyle and .npz!")
            return None

        hd.binding = self.calc_binding_hair(hd.data)
        self.hair_cache[fit_id] = hd
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

    def fit_hair(self, obj, idx):
        t = utils.Timer()
        psys = obj.particle_systems[idx]
        hd = self.get_hair_data(psys)
        if not hd:
            return False

        obj.particle_systems.active_index = idx

        restore_modifiers = utils.disable_modifiers(obj, lambda m: m.type == "SHRINKWRAP")
        try:
            update_hair(obj, hd.cnts, hd.get_morphed(self.get_diff_hair()))
        finally:
            for m in restore_modifiers:
                m.show_viewport = True

        t.time("hair_fit")
        return True

    def fit_obj_hair(self, obj):
        has_fit = False
        for i in range(len(obj.particle_systems)):
            has_fit |= self.fit_hair(obj, i)
        return has_fit
