"""Render a grid of generated worlds into one labelled poster (a "seed atlas") in Blender.

Generate the worlds first, then run this script inside Blender (headless, no GPU or
display needed; it renders with Cycles on the CPU):

    roam-usd generate --roam <roam-checkout> --seeds 1..16 --radius 3 --cave-depth 0 -o out/
    blender --background --python scripts/seed_atlas.py -- out/seed_*.usda -o atlas.png

The stages are referenced side by side from one temporary layout stage, imported
together and seen from straight above with an orthographic camera. Under each world a
label gives its seed, its surface rooms, how many of them are of each biome (the
rooms' ``roam:roomType``) and its entity count, read from the stage with Blender's
bundled ``pxr``. ``--stats stats.json`` also writes those numbers.

By default every mesh shows its USD ``displayColor`` (roam-usd's catalog colours) and
bound materials such as creature sprites are ignored, so the poster contains no Roam
artwork. ``--textured --roam <roam-checkout>`` instead keeps the sprite materials and
textures tile prototypes with the matching images from that checkout at render time;
Roam's art is under Roam's own license, so posters made that way are for your own use
and are not committed to this repository.

Cycles writes a full-colour PNG; ``docs/images/seed-atlas.png`` was additionally
reduced to 96 colours (Pillow, median cut, no dithering) to keep it under 2 MB.
"""

import argparse
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import bpy
from mathutils import Vector
from pxr import Gf, Usd, UsdGeom

BIOME_ORDER = ("grassland", "forest", "jungle", "mountain", "empty", "cave", "unknown")
LABEL_LINES = 2.4  # label block height, in units of the seed line's text size


def stage_stats(path):
    """Seed, rooms and biomes (surface level), and instanced/individual entity counts."""
    stage = Usd.Stage.Open(str(path))
    data = stage.GetRootLayer().customLayerData
    seed = data.get("roam:seed")
    if seed is None:
        match = re.search(r"seed_(-?\d+)", Path(path).stem)
        seed = int(match.group(1)) if match else None
    biomes = Counter()
    rooms_by_level = Counter()
    entities = Counter()
    for prim in stage.Traverse():
        name = prim.GetName()
        if name.startswith("Room_"):
            level = prim.GetParent().GetName()
            rooms_by_level[level] += 1
            if level == "Surface":
                biomes[str(prim.GetCustomDataByKey("roam:roomType") or "unknown")] += 1
        elif prim.IsA(UsdGeom.PointInstancer):
            instancer = UsdGeom.PointInstancer(prim)
            targets = instancer.GetPrototypesRel().GetTargets()
            indices = instancer.GetProtoIndicesAttr().Get() or []
            for index, count in Counter(indices).items():
                entities[targets[index].name] += count
        elif prim.GetParent().GetName() in ("Creatures", "Structures"):
            entities[name.split("_")[0]] += 1
    return {
        "seed": seed,
        "stage": str(path),
        "radius": data.get("roam:radius"),
        "rooms": rooms_by_level.get("Surface", 0),
        "rooms_by_level": dict(rooms_by_level),
        "biomes": {b: biomes[b] for b in sorted(biomes, key=_biome_key)},
        "entities": sum(entities.values()),
        "entities_by_class": dict(sorted(entities.items())),
    }


def _biome_key(name):
    return (BIOME_ORDER.index(name) if name in BIOME_ORDER else len(BIOME_ORDER), name)


def world_bounds(path):
    """The stage's x/y bounds (min, max) as (Vector, Vector), from ``/World``."""
    stage = Usd.Stage.Open(str(path))
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    box = cache.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
    return Vector(box.GetMin()), Vector(box.GetMax())


def write_layout(stages, offsets, path):
    """A stage referencing each world under ``/Atlas/World_<i>``, moved to its cell."""
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Atlas")
    stage.SetDefaultPrim(root.GetPrim())
    for i, (source, offset) in enumerate(zip(stages, offsets, strict=True)):
        xform = UsdGeom.Xform.Define(stage, f"/Atlas/World_{i}")
        xform.AddTranslateOp().Set(Gf.Vec3d(*offset))
        xform.GetPrim().GetReferences().AddReference(str(Path(source).resolve()))
    stage.GetRootLayer().Save()


def display_color_material():
    material = bpy.data.materials.new("AtlasDisplayColor")
    material.use_nodes = True
    tree = material.node_tree
    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_name = "displayColor"
    tree.links.new(attribute.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    return material


def tile_texture_material(image_path):
    """A pixel-art material: the image across each face's footprint, nearest-neighbour."""
    material = bpy.data.materials.new("AtlasTile_" + image_path.stem)
    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    coords = tree.nodes.new("ShaderNodeTexCoord")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(image_path), check_existing=True)
    texture.interpolation = "Closest"
    texture.extension = "CLIP"
    tree.links.new(coords.outputs["Generated"], texture.inputs["Vector"])
    tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
    tree.links.new(texture.outputs["Alpha"], bsdf.inputs["Alpha"])
    return material


def roam_image(roam, class_name):
    """``assets/images/<class name with a lower-case first letter>.png`` in a Roam checkout."""
    path = Path(roam) / "assets" / "images" / (class_name[:1].lower() + class_name[1:] + ".png")
    return path if path.is_file() else None


def apply_materials(textured, roam):
    flat = display_color_material()
    tile_materials = {}
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        class_name = re.sub(r"(_n?\d+)+$", "", o.name.split(".")[0])
        if textured and roam and class_name != "Ground" and not o.data.materials:
            if class_name not in tile_materials:
                image = roam_image(roam, class_name)
                tile_materials[class_name] = image and tile_texture_material(image)
            if tile_materials[class_name]:
                o.data.materials.append(tile_materials[class_name])
                continue
        if not textured:
            o.data.materials.clear()
        if not o.data.materials:
            o.data.materials.append(flat)


def text_object(body, size, location, color, align="LEFT", name="Label"):
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = body
    curve.size = size
    curve.align_x = align
    curve.align_y = "TOP"
    obj = bpy.data.objects.new(name, curve)
    obj.location = location
    material = bpy.data.materials.new(name + "Ink")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.remove(tree.nodes["Principled BSDF"])
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*color, 1.0)
    tree.links.new(emission.outputs["Emission"], tree.nodes["Material Output"].inputs["Surface"])
    curve.materials.append(material)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def label_lines(stats):
    biomes = " · ".join(f"{n} {b}" for b, n in stats["biomes"].items())
    return (
        f"seed {stats['seed']}",
        f"{stats['rooms']} rooms · {len(stats['biomes'])} biomes · {stats['entities']:,} entities",
        biomes,
    )


def build(args):
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    stats = [stage_stats(p) for p in args.stages]

    bounds = [world_bounds(p) for p in args.stages]
    size = max(max(hi.x - lo.x, hi.y - lo.y) for lo, hi in bounds)
    gap = size * 0.08
    text = size * 0.055
    label_h = text * (LABEL_LINES + 1.6)
    cell_w, cell_h = size + gap, size + label_h + gap
    columns = args.columns
    rows = math.ceil(len(bounds) / columns)
    title_h = text * 3.2 if args.title else 0.0
    top_z = max(hi.z for _, hi in bounds) + 1.0

    offsets = []
    for i, ((lo, hi), world_stats) in enumerate(zip(bounds, stats, strict=True)):
        row, column = divmod(i, columns)
        # Cell's top-left corner; the world sits centred in a size x size square there.
        left, top = column * cell_w, -title_h - row * cell_h
        offsets.append(
            (left + size / 2 - (lo.x + hi.x) / 2, top - size / 2 - (lo.y + hi.y) / 2, 0.0)
        )
        seed_line, counts_line, biome_line = label_lines(world_stats)
        y = top - size - text * 0.5
        text_object(seed_line, text * 1.25, (left, y, top_z), (0.08,) * 3)
        text_object(counts_line, text * 0.8, (left, y - text * 1.5, top_z), (0.2,) * 3)
        text_object(biome_line, text * 0.8, (left, y - text * 2.6, top_z), (0.35,) * 3)

    with tempfile.TemporaryDirectory() as tmp:
        layout = Path(tmp) / "atlas.usda"
        write_layout(args.stages, offsets, layout)
        bpy.ops.wm.usd_import(filepath=str(layout))
    imported = time.monotonic()

    if args.title:
        text_object(args.title, text * 1.6, (0.0, -text * 0.4, top_z), (0.05,) * 3)

    apply_materials(args.textured, args.roam)

    width, height = columns * cell_w - gap, title_h + rows * cell_h - gap
    margin = gap
    cam_data = bpy.data.cameras.new("Camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(width, height) + 2 * margin
    cam_data.clip_end = size * 10
    cam = bpy.data.objects.new("Camera", cam_data)
    cam.location = (width / 2, -height / 2, size * 3)
    scene.collection.objects.link(cam)
    scene.camera = cam

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 2.2
    sun.rotation_euler = (math.radians(14), math.radians(8), 0.0)
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    tree = world.node_tree
    backdrop = tree.nodes["Background"]
    backdrop.inputs["Color"].default_value = (0.96, 0.95, 0.92, 1)
    fill = tree.nodes.new("ShaderNodeBackground")
    fill.inputs["Strength"].default_value = 0.45
    light_path = tree.nodes.new("ShaderNodeLightPath")
    mix = tree.nodes.new("ShaderNodeMixShader")
    tree.links.new(light_path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    tree.links.new(fill.outputs["Background"], mix.inputs[1])
    tree.links.new(backdrop.outputs["Background"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], tree.nodes["World Output"].inputs["Surface"])
    scene.world = world

    scale = args.width / (max(width, height) + 2 * margin)
    scene.render.resolution_x = round((width + 2 * margin) * scale)
    scene.render.resolution_y = round((height + 2 * margin) * scale)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True  # also keeps the PNG small
    scene.render.threads_mode = "FIXED"
    scene.render.threads = args.threads
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 100
    scene.render.filepath = str(Path(args.output).resolve())
    bpy.ops.render.render(write_still=True)
    finished = time.monotonic()

    summary = {
        "output": scene.render.filepath,
        "worlds": stats,
        "render": {
            "blender": bpy.app.version_string,
            "engine": "CYCLES",
            "device": "CPU",
            "samples": args.samples,
            "threads": args.threads,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "camera": "orthographic, top-down",
            "textured": bool(args.textured),
        },
        "seconds": {
            "import": round(imported - started, 1),
            "render": round(finished - imported, 1),
        },
    }
    if args.stats:
        Path(args.stats).write_text(json.dumps(summary, indent=2) + "\n")
    print("ROAM_USD_ATLAS " + json.dumps(summary["seconds"]), flush=True)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stages", nargs="+", help="stages to place, in reading order")
    parser.add_argument("-o", "--output", default="atlas.png")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument(
        "--width", type=int, default=2400, help="pixels along the poster's longer side"
    )
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--title", default="")
    parser.add_argument("--stats", metavar="JSON", help="also write per-world stats here")
    parser.add_argument("--textured", action="store_true", help="keep sprites, texture tiles")
    parser.add_argument("--roam", metavar="ROAM_CHECKOUT", help="tile images for --textured")
    args = parser.parse_args(argv)
    if args.textured and not args.roam:
        parser.error("--textured needs --roam <roam-checkout> for the tile images")
    build(args)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
