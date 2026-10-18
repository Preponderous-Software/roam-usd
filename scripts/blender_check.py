"""Import a USD stage into Blender (headless ``bpy``) and print a JSON summary.

Run with an interpreter that has Blender's ``bpy`` module (``pip install bpy==4.2.0``,
Python 3.11); it is used by the ``blender``-marked smoke test and works on its own:

    python scripts/blender_check.py world.usda

Optionally renders a screenshot with Cycles on the CPU (no GPU or display needed):

    python scripts/blender_check.py world.usda --render shot.png [--elevation 55]

``--flat`` ignores bound materials (such as sprite textures) when rendering. Rendering
adds a viewer-side material that shows USD ``displayColor`` on meshes with
no material of their own; the stage itself is not changed.
"""

import argparse
import json
import math
import sys
from collections import Counter

import bpy
from mathutils import Vector


def import_stage(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.wm.usd_import(filepath=path)
    return "FINISHED" in result


def summary(ok):
    objects = list(bpy.data.objects)
    by_name = {o.name.split(".")[0] for o in objects}
    levels = {
        o.name: round(o.matrix_world.translation.z, 3)
        for o in objects
        if o.type == "EMPTY" and (o.name == "Surface" or o.name.startswith("Cave_"))
    }
    return {
        "imported": ok,
        "blender": bpy.app.version_string,
        "objects": len(objects),
        "types": dict(Counter(o.type for o in objects)),
        "levels": levels,
        "grounds": sum(o.name.split(".")[0] == "Ground" for o in objects),
        "instancers": sum(o.type == "POINTCLOUD" for o in objects),
        "creatures": sorted(
            o.name for o in objects if o.parent and o.parent.name.startswith("Creatures")
        ),
        "structures": sorted(
            o.name for o in objects if o.parent and o.parent.name.startswith("Structures")
        ),
        "player": "Player" in by_name or "Marker" in by_name,
        "materials": sorted(m.name for m in bpy.data.materials),
        "images": sorted(i.name for i in bpy.data.images),
    }


def render(out, elevation, azimuth, flat=False):
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in bpy.data.objects:
        if o.type not in ("MESH", "POINTCLOUD"):
            continue
        ev = o.evaluated_get(depsgraph)
        for corner in ev.bound_box:
            w = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
    center = (lo + hi) / 2
    size = max(hi.x - lo.x, hi.y - lo.y, 1.0)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.clip_end = size * 10
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    el, az = math.radians(elevation), math.radians(azimuth)
    dist = size * 3
    cam.location = center + dist * Vector(
        (math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))
    )
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    # Fit the frame to the scene's bounding box as the camera sees it.
    bpy.context.view_layer.update()
    to_camera = cam.matrix_world.inverted()
    corners = [
        to_camera @ Vector((x, y, z))
        for x in (lo.x, hi.x)
        for y in (lo.y, hi.y)
        for z in (lo.z, hi.z)
    ]
    width = max(c.x for c in corners) - min(c.x for c in corners)
    height = max(c.y for c in corners) - min(c.y for c in corners)
    cam_data.ortho_scale = 1.06 * max(width, height * 1280 / 800)

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(35), math.radians(15), math.radians(30))
    scene.collection.objects.link(sun)

    material = bpy.data.materials.new("ViewerDisplayColor")
    material.use_nodes = True
    nodes = material.node_tree
    attribute = nodes.nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "displayColor"
    bsdf = nodes.nodes["Principled BSDF"]
    nodes.links.new(attribute.outputs["Color"], bsdf.inputs["Base Color"])
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if flat:  # drop bound materials (e.g. sprite textures) and show displayColor only
            o.data.materials.clear()
        if not o.data.materials:
            o.data.materials.append(material)

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.75, 0.8, 0.85, 1)
    background.inputs["Strength"].default_value = 0.8
    scene.world = world

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 800
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage")
    parser.add_argument("--render", metavar="PNG")
    parser.add_argument("--elevation", type=float, default=55.0)
    parser.add_argument("--azimuth", type=float, default=20.0)
    parser.add_argument(
        "--flat", action="store_true", help="render displayColor only, ignoring textures"
    )
    args = parser.parse_args(argv)
    ok = import_stage(args.stage)
    result = summary(ok)
    if args.render:
        render(args.render, args.elevation, args.azimuth, args.flat)
        result["render"] = args.render
    print("ROAM_USD_BLENDER_SUMMARY " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
