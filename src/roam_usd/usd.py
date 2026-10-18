"""Write a :class:`~roam_usd.model.World` as an OpenUSD stage.

Layout and naming follow ARCHITECTURE.md § Roam → USD mapping: Z-up, one tile =
1 × 1 unit, ``/World/<Level>/Room_<x>_<y>``, one ``PointInstancer`` per room for the
instanced tiles, levels stacked ``LEVEL_SPACING`` units apart on Z. Rooms, tiles and
prototypes are emitted in sorted order so the same input gives byte-identical
``.usda``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Tf, Usd, UsdGeom, Vt

from roam_usd import __version__, catalog
from roam_usd.catalog import CatalogEntry
from roam_usd.model import Room, World

log = logging.getLogger(__name__)

#: Vertical distance between levels: Cave_1 sits at Z = -4, Cave_2 at -8, ...
LEVEL_SPACING = 4.0

#: Instance ids are ``(tileY * columns + tileX) * ID_STACK_STRIDE + stackIndex``.
ID_STACK_STRIDE = 16

FORMATS = {"usda": ".usda", "usdc": ".usdc"}


class ExportError(ValueError):
    """The stage cannot be written as requested (e.g. extension and format disagree)."""


@dataclass
class ExportOptions:
    #: ``"all"`` levels, or only the ``"surface"``.
    levels: str = "all"
    #: Extra ``customLayerData`` entries (source, seed, Roam commit/version, ...).
    layer_data: Mapping[str, Any] = field(default_factory=dict)


def prim_coord(value: int) -> str:
    """Coordinate as used in prim names: ``-1`` → ``n1`` (``-`` is not valid in prim names)."""
    return f"n{-value}" if value < 0 else str(value)


def room_prim_name(room: Room) -> str:
    return f"Room_{prim_coord(room.x)}_{prim_coord(room.y)}"


def tile_center(tx: int, ty: int) -> tuple[float, float]:
    """Centre of tile (tx, ty) in room space: Roam's +y (screen-down) becomes USD -Y."""
    return tx + 0.5, -(ty + 0.5)


def instance_id(tx: int, ty: int, columns: int, stack_index: int) -> int:
    return (ty * columns + tx) * ID_STACK_STRIDE + stack_index


def prototype_name(entity_class: str) -> str:
    """A valid prim name for a class name (unknown classes may contain anything)."""
    return (
        entity_class if Tf.IsValidIdentifier(entity_class) else Tf.MakeValidIdentifier(entity_class)
    )


# --- geometry -----------------------------------------------------------------

_BOX_FACES = [
    (0, 3, 2, 1),  # bottom (-Z)
    (4, 5, 6, 7),  # top (+Z)
    (0, 1, 5, 4),  # -Y
    (1, 2, 6, 5),  # +X
    (2, 3, 7, 6),  # +Y
    (3, 0, 4, 7),  # -X
]


def _box_points(width: float, height: float, lift: float) -> list[Gf.Vec3f]:
    h = width / 2.0
    z0, z1 = lift, lift + height
    corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
    return [Gf.Vec3f(x, y, z0) for x, y in corners] + [Gf.Vec3f(x, y, z1) for x, y in corners]


def _define_mesh(
    stage: Usd.Stage,
    path: Sdf.Path,
    points: list[Gf.Vec3f],
    faces: list[tuple[int, ...]],
    color: tuple[float, float, float],
) -> UsdGeom.Mesh:
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray(points))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([len(f) for f in faces]))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(Vt.Vec3fArray(points)))
    mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*color)]))
    return mesh


def define_box(stage: Usd.Stage, path: Sdf.Path, entry: CatalogEntry) -> UsdGeom.Mesh:
    """A box standing on the origin: ``entry.width`` square, ``entry.height`` tall."""
    height = max(entry.height, 0.01)
    return _define_mesh(
        stage, path, _box_points(entry.width, height, entry.lift), _BOX_FACES, entry.color
    )


# --- stage --------------------------------------------------------------------


class StageWriter:
    def __init__(self, world: World, options: ExportOptions | None = None):
        self.world = world
        self.options = options or ExportOptions()
        if self.options.levels not in ("all", "surface"):
            raise ExportError(f"--levels must be 'all' or 'surface', not {self.options.levels!r}")
        self.stage = Usd.Stage.CreateInMemory()
        self._prototypes: dict[str, Sdf.Path] = {}

    # Public entry point
    def build(self) -> Usd.Stage:
        stage = self.stage
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        world_prim = UsdGeom.Xform.Define(stage, "/World").GetPrim()
        stage.SetDefaultPrim(world_prim)
        stage.GetRootLayer().customLayerData = self._layer_data()

        catalog.reset_warnings()
        stage.CreateClassPrim("/World/Prototypes")
        for level in self._levels():
            level_path = Sdf.Path("/World").AppendChild(level.name)
            level_xform = UsdGeom.Xform.Define(stage, level_path)
            level_xform.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, level.z * LEVEL_SPACING))
            level_xform.GetPrim().SetCustomDataByKey("roam:z", level.z)
            for room in sorted(level.rooms, key=lambda r: (r.x, r.y)):
                self._write_room(level_path, room)
        return stage

    def _levels(self):
        if self.options.levels == "surface":
            return [lvl for lvl in self.world.levels if lvl.z == 0]
        return list(self.world.levels)

    def _layer_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"roam-usd:version": __version__}
        data.update(self.options.layer_data)
        if "roam:source" not in data:
            data["roam:source"] = "save"
            data["roam:savePath"] = self.world.source
        if self.world.tick is not None:
            data.setdefault("roam:tick", self.world.tick)
        return data

    def _shared_prototype(self, entity_class: str) -> tuple[Sdf.Path, CatalogEntry]:
        entry = catalog.lookup(entity_class)
        key = entry.name
        if key not in self._prototypes:
            path = Sdf.Path("/World/Prototypes").AppendChild(key)
            mesh = define_box(self.stage, path, entry)
            mesh.GetPrim().SetCustomDataByKey("roam:category", entry.category)
            self._prototypes[key] = path
        return self._prototypes[key], entry

    def _write_room(self, level_path: Sdf.Path, room: Room) -> None:
        stage = self.stage
        room_path = level_path.AppendChild(room_prim_name(room))
        xform = UsdGeom.Xform.Define(stage, room_path)
        xform.AddTranslateOp().Set(Gf.Vec3d(room.x * room.columns, -room.y * room.rows, 0.0))
        prim = xform.GetPrim()
        prim.SetCustomDataByKey("roam:roomType", room.room_type)
        prim.SetCustomDataByKey("roam:x", room.x)
        prim.SetCustomDataByKey("roam:y", room.y)
        prim.SetCustomDataByKey("roam:z", room.z)

        self._write_ground(room_path, room)
        self._write_tiles(room_path, room)

    def _write_ground(self, room_path: Sdf.Path, room: Room) -> None:
        w, h = float(room.columns), float(room.rows)
        points = [Gf.Vec3f(0, -h, 0), Gf.Vec3f(w, -h, 0), Gf.Vec3f(w, 0, 0), Gf.Vec3f(0, 0, 0)]
        color = tuple(c / 255.0 for c in room.background_color)
        _define_mesh(self.stage, room_path.AppendChild("Ground"), points, [(0, 1, 2, 3)], color)

    def _write_tiles(self, room_path: Sdf.Path, room: Room) -> None:
        # (class, tile x, tile y, stack z, stack index) for every instanced entity.
        instances: list[tuple[str, int, int, float, int]] = []
        for tile in sorted(room.tiles, key=lambda t: (t.y, t.x)):
            z = 0.0
            for entity in tile.entities:
                entry = catalog.lookup(entity.entity_class)
                if not entry.individual:
                    instances.append((entity.entity_class, tile.x, tile.y, z, entity.stack_index))
                z += entry.stack_height
        if not instances:
            return

        classes = sorted({cls for cls, *_ in instances})
        inst_path = room_path.AppendChild("Tiles")
        instancer = UsdGeom.PointInstancer.Define(self.stage, inst_path)
        protos_path = inst_path.AppendChild("Prototypes")
        UsdGeom.Scope.Define(self.stage, protos_path)
        targets = []
        used_names: set[str] = set()
        for cls in classes:
            shared, entry = self._shared_prototype(cls)
            name = prototype_name(cls)
            while name in used_names:
                name += "_"
            used_names.add(name)
            # Typeless, so the referenced Mesh supplies the prim type.
            proto = self.stage.DefinePrim(protos_path.AppendChild(name))
            proto.GetReferences().AddInternalReference(shared)
            proto.SetCustomDataByKey("roam:entityClass", cls)
            targets.append(proto.GetPath())
        instancer.CreatePrototypesRel().SetTargets(targets)

        index = {cls: i for i, cls in enumerate(classes)}
        proto_indices, positions, ids = [], [], []
        for cls, tx, ty, z, k in instances:
            cx, cy = tile_center(tx, ty)
            proto_indices.append(index[cls])
            positions.append(Gf.Vec3f(cx, cy, z))
            if k >= ID_STACK_STRIDE:
                log.warning(
                    "%s: tile (%d, %d) has more than %d stacked entities; instance ids may repeat",
                    room.source_file,
                    tx,
                    ty,
                    ID_STACK_STRIDE,
                )
            ids.append(instance_id(tx, ty, room.columns, k))
        instancer.CreateProtoIndicesAttr(Vt.IntArray(proto_indices))
        instancer.CreatePositionsAttr(Vt.Vec3fArray(positions))
        instancer.CreateIdsAttr(Vt.Int64Array(ids))
        extent = instancer.ComputeExtentAtTime(Usd.TimeCode.Default(), Usd.TimeCode.Default())
        if extent:
            instancer.CreateExtentAttr(extent)


def build_stage(world: World, options: ExportOptions | None = None) -> Usd.Stage:
    """Build the stage for ``world`` in memory."""
    return StageWriter(world, options).build()


def output_format(path: str | Path, binary: bool) -> str:
    """``"usda"`` or ``"usdc"``, checking the file extension agrees with ``--binary``."""
    fmt = "usdc" if binary else "usda"
    suffix = Path(path).suffix.lower()
    if suffix == ".usd":
        return fmt
    if suffix != FORMATS[fmt]:
        flag = "with" if binary else "without"
        raise ExportError(
            f"{path}: a {fmt} stage written {flag} --binary needs a {FORMATS[fmt]} or .usd name"
        )
    return fmt


def write_stage(stage: Usd.Stage, path: str | Path, binary: bool = False) -> Path:
    out = Path(path)
    fmt = output_format(out, binary)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not stage.GetRootLayer().Export(str(out), args={"format": fmt}):
        raise ExportError(f"{out}: could not write the stage")
    return out


def export_world(
    world: World,
    path: str | Path,
    binary: bool = False,
    options: ExportOptions | None = None,
) -> Path:
    """Build and write ``world``; returns the written path."""
    output_format(path, binary)  # fail before doing any work
    return write_stage(build_stage(world, options), path, binary)
