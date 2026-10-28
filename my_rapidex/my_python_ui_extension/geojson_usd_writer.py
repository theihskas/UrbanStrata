"""
geojson_usd_writer.py
=====================
Converts GeoJSON network files (edges, nodes, boundary, zones, POIs) to
batched USD files and adds them as sublayers on the active Isaac Sim stage.

All functions are pure (no Omniverse extension class state). Call
``convert_network_folder_to_usd`` from extension.py.
"""

import math
import json
import os
import glob as _glob
import gc

import carb
from pxr import Gf, Sdf

# ── Per-layer colour palette ──────────────────────────────────────────────────
FILE_COLORS = {
    "edges":                Gf.Vec3f(1.0, 0.1, 0.1),   # red        — road network
    "bike_edges":           Gf.Vec3f(0.2, 1.0, 0.4),   # green      — bike lanes
    "nodes":                Gf.Vec3f(1.0, 1.0, 0.2),   # yellow     — intersections
    "boundary":             Gf.Vec3f(1.0, 0.4, 0.1),   # orange     — city boundary
    "poi_osm":              Gf.Vec3f(1.0, 0.2, 0.8),   # pink       — OSM POIs
    "zones_link_selection": Gf.Vec3f(0.5, 0.3, 0.9),   # purple     — zone selection
    # ── Zones & SafeGraph (added for zoning workflow) ─────────────────────────
    "zones":                Gf.Vec3f(1.0, 0.80, 0.0),  # gold       — zone area fills
    "zones_boundary":       Gf.Vec3f(0.0, 1.0,  0.9),  # cyan       — zone edge outlines
    "poi_safegraph":        Gf.Vec3f(1.0, 0.1,  0.1),  # bright red — SafeGraph POI spheres
}
DEFAULT_COLOR = Gf.Vec3f(0.7, 0.7, 0.7)

# ── Noise decay band palette (distance → colour, close=red, far=green) ────────
# Bands: "0-10m" | "10-25m" | "25-50m" | "50-100m" | "100-200m"
NOISE_BAND_COLORS = {
    "0-10m":    Gf.Vec3f(0.85, 0.05, 0.05),   # deep red      — loudest / closest
    "10-25m":   Gf.Vec3f(1.00, 0.38, 0.00),   # dark orange
    "25-50m":   Gf.Vec3f(1.00, 0.70, 0.10),   # amber
    "50-100m":  Gf.Vec3f(0.75, 0.90, 0.10),   # yellow-green
    "100-200m": Gf.Vec3f(0.15, 0.80, 0.25),   # green         — quietest / farthest
}
# Extrusion height per band — creates a stepped 3D "wave" receding from the road
NOISE_BAND_HEIGHTS = {
    "0-10m":    55.0,
    "10-25m":   40.0,
    "25-50m":   26.0,
    "50-100m":  14.0,
    "100-200m":  5.0,
}

# ── Default geometry parameters ───────────────────────────────────────────────
TUBE_SIDES          = 12
TUBE_RADIUS         = 5.0    # metres — road pipe thickness
POINT_SIZE          = 10.0   # sphere / cylinder radius for network nodes
NODE_HEIGHT         = 20.0   # intersection cylinder height
POLY_HEIGHT         = 2.0    # generic polygon extrusion
ZONE_FILL_HEIGHT    = 10.0   # zone polygon extrusion (taller, gold fill)
BOUNDARY_TUBE_RADIUS = 4.0   # tube radius for zone outline perimeter
BOUNDARY_ELEVATION  = 11.0   # z-offset so outline sits on top of zone fill
POI_SPHERE_SIZE     = 25.0   # SafeGraph POI sphere radius (larger than network nodes)


# ── Coordinate projection ─────────────────────────────────────────────────────

def lonlat_to_xyz(lon, lat, origin_lon, origin_lat, height=0.0):
    """Convert WGS-84 lon/lat to local Cartesian metres centred on origin."""
    try:
        lon, lat, height = float(lon), float(lat), float(height)
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return (0.0, 0.0, 0.0)
    except (ValueError, TypeError):
        return (0.0, 0.0, 0.0)
    R = 6_378_137.0
    lat_r = math.radians(origin_lat)
    x = (lon - origin_lon) * R * math.cos(lat_r) * math.pi / 180.0
    y = (lat - origin_lat) * R * math.pi / 180.0
    return (x, y, height)


# ── Geometry builders ─────────────────────────────────────────────────────────

def build_tube(points, tube_sides=TUBE_SIDES, tube_radius=TUBE_RADIUS):
    """Return (verts, face_counts, face_indices) for a swept-tube along *points*."""
    # Deduplicate consecutive identical points
    filtered = [points[0]]
    for p in points[1:]:
        if p != filtered[-1]:
            filtered.append(p)
    points = filtered
    n = len(points)
    if n < 2:
        return [], [], []

    S, R = tube_sides, tube_radius
    verts, face_counts, face_indices = [], [], []

    for pi in range(n):
        cx, cy, cz = points[pi]
        if pi == 0:
            dx = points[1][0] - points[0][0]
            dy = points[1][1] - points[0][1]
            dz = points[1][2] - points[0][2]
        elif pi == n - 1:
            dx = points[pi][0] - points[pi - 1][0]
            dy = points[pi][1] - points[pi - 1][1]
            dz = points[pi][2] - points[pi - 1][2]
        else:
            dx = points[pi + 1][0] - points[pi - 1][0]
            dy = points[pi + 1][1] - points[pi - 1][1]
            dz = points[pi + 1][2] - points[pi - 1][2]

        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length > 0.001:
            dx, dy, dz = dx / length, dy / length, dz / length
        else:
            dx, dy, dz = 1.0, 0.0, 0.0

        if abs(dz) < 0.9:
            rx, ry, rz = -dy, dx, 0.0
        else:
            rx, ry, rz = 1.0, 0.0, 0.0
        rl = math.sqrt(rx * rx + ry * ry + rz * rz)
        if rl > 0.001:
            rx, ry, rz = rx / rl, ry / rl, rz / rl
        ux = dy * rz - dz * ry
        uy = dz * rx - dx * rz
        uz = dx * ry - dy * rx

        for s in range(S):
            a = 2.0 * math.pi * s / S
            ca, sa = math.cos(a), math.sin(a)
            verts.append(Gf.Vec3f(
                cx + R * (rx * ca + ux * sa),
                cy + R * (ry * ca + uy * sa),
                cz + R * (rz * ca + uz * sa),
            ))

    for pi in range(n - 1):
        for s in range(S):
            ns = (s + 1) % S
            v0 = pi * S + s
            v1 = pi * S + ns
            v2 = (pi + 1) * S + ns
            v3 = (pi + 1) * S + s
            face_counts.extend([3, 3])
            face_indices.extend([v0, v1, v2, v0, v2, v3])

    return verts, face_counts, face_indices


def build_sphere(cx, cy, cz, radius, tube_sides=TUBE_SIDES, rings=8):
    """Return (verts, face_counts, face_indices) for a UV sphere."""
    S = tube_sides
    verts, fc, fi = [], [], []
    verts.append(Gf.Vec3f(cx, cy, cz + radius))  # top pole
    for ring in range(1, rings):
        phi = math.pi * ring / rings
        z = cz + radius * math.cos(phi)
        r = radius * math.sin(phi)
        for s in range(S):
            t = 2.0 * math.pi * s / S
            verts.append(Gf.Vec3f(cx + r * math.cos(t), cy + r * math.sin(t), z))
    bot = len(verts)
    verts.append(Gf.Vec3f(cx, cy, cz - radius))  # bottom pole
    for s in range(S):
        ns = (s + 1) % S
        fc.append(3)
        fi.extend([0, 1 + s, 1 + ns])
    for ring in range(rings - 2):
        base = 1 + ring * S
        nb = base + S
        for s in range(S):
            ns = (s + 1) % S
            fc.extend([3, 3])
            fi.extend([base + s, nb + s, nb + ns, base + s, nb + ns, base + ns])
    lb = 1 + (rings - 2) * S
    for s in range(S):
        ns = (s + 1) % S
        fc.append(3)
        fi.extend([lb + s, bot, lb + ns])
    return verts, fc, fi


def build_cylinder(cx, cy, cz, radius, height, tube_sides=TUBE_SIDES):
    """Return (verts, face_counts, face_indices) for a capped cylinder."""
    S = tube_sides
    verts, fc, fi = [], [], []
    verts.append(Gf.Vec3f(cx, cy, cz))  # bottom centre
    for s in range(S):
        a = 2.0 * math.pi * s / S
        verts.append(Gf.Vec3f(cx + radius * math.cos(a), cy + radius * math.sin(a), cz))
    for s in range(S):
        a = 2.0 * math.pi * s / S
        verts.append(Gf.Vec3f(cx + radius * math.cos(a), cy + radius * math.sin(a), cz + height))
    top_c = 2 * S + 1
    verts.append(Gf.Vec3f(cx, cy, cz + height))  # top centre
    bot, top = 1, S + 1
    for s in range(S):
        ns = (s + 1) % S
        fc.append(3)
        fi.extend([0, bot + ns, bot + s])
    for s in range(S):
        ns = (s + 1) % S
        fc.extend([3, 3])
        fi.extend([bot + s, bot + ns, top + ns, bot + s, top + ns, top + s])
    for s in range(S):
        ns = (s + 1) % S
        fc.append(3)
        fi.extend([top_c, top + s, top + ns])
    return verts, fc, fi


def build_polygon(ring, origin_lon, origin_lat, poly_height=POLY_HEIGHT):
    """Return (verts, face_counts, face_indices) for an extruded polygon ring."""
    pts = []
    for coord in ring:
        x, y, z = lonlat_to_xyz(coord[0], coord[1], origin_lon, origin_lat,
                                  coord[2] if len(coord) > 2 else 0.0)
        pts.append((x, y, z))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return [], [], []
    # Limit complexity to prevent memory issues
    MAX_VERTS = 1000
    if n > MAX_VERTS:
        step = max(1, n // MAX_VERTS)
        pts = [pts[i] for i in range(0, n, step)]
        n = len(pts)
        if n < 3:
            return [], [], []

    h = poly_height
    verts = ([Gf.Vec3f(x, y, z) for x, y, z in pts] +
             [Gf.Vec3f(x, y, z + h) for x, y, z in pts])
    fc, fi = [], []
    for i in range(n):
        ni = (i + 1) % n
        fc.extend([3, 3])
        fi.extend([i, ni, ni + n, i, ni + n, i + n])
    for i in range(1, n - 1):
        fc.append(3)
        fi.extend([n, n + i, n + i + 1])
    for i in range(1, n - 1):
        fc.append(3)
        fi.extend([0, i + 1, i])
    return verts, fc, fi


def build_polygon_outline(ring, origin_lon, origin_lat,
                          tube_radius=BOUNDARY_TUBE_RADIUS,
                          elevation=BOUNDARY_ELEVATION):
    """
    Return (verts, face_counts, face_indices) for a tube that traces the
    perimeter of a polygon ring — used to draw zone boundary outlines.

    The tube is elevated *elevation* metres above z=0 so it sits visibly
    on top of the zone fill (which is ZONE_FILL_HEIGHT tall).
    """
    pts = []
    for coord in ring:
        x, y, z = lonlat_to_xyz(coord[0], coord[1], origin_lon, origin_lat,
                                  coord[2] if len(coord) > 2 else 0.0)
        pts.append((x, y, z + elevation))
    if len(pts) < 2:
        return [], [], []
    # Limit vertex count to prevent memory issues on complex polygons
    MAX_PTS = 500
    if len(pts) > MAX_PTS:
        step = max(1, len(pts) // MAX_PTS)
        pts = [pts[i] for i in range(0, len(pts), step)]
    # Ensure the ring is closed (first == last)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return build_tube(pts, tube_sides=6, tube_radius=tube_radius)


# ── Sdf primitive creator ─────────────────────────────────────────────────────

def sdf_mesh(parent_spec, name, verts, fc, fi, display_color, custom_attrs=None):
    """
    Append a Mesh prim to *parent_spec* inside an Sdf.Layer edit.

    Parameters
    ----------
    custom_attrs : dict | None
        Optional mapping of attribute_name → (SdfValueTypeName, value) for
        per-prim custom data (e.g. building metadata).  Keys that start with
        'custom:' are written verbatim; others have 'custom:' prepended.
    """
    ms = Sdf.PrimSpec(parent_spec, name, Sdf.SpecifierDef)
    ms.typeName = "Mesh"
    Sdf.AttributeSpec(ms, "points",            Sdf.ValueTypeNames.Point3fArray).default = verts
    Sdf.AttributeSpec(ms, "faceVertexCounts",  Sdf.ValueTypeNames.IntArray).default = fc
    Sdf.AttributeSpec(ms, "faceVertexIndices", Sdf.ValueTypeNames.IntArray).default = fi
    Sdf.AttributeSpec(ms, "primvars:displayColor",
                      Sdf.ValueTypeNames.Color3fArray).default = [display_color]
    Sdf.AttributeSpec(ms, "subdivisionScheme", Sdf.ValueTypeNames.Token).default = "none"
    if custom_attrs:
        for attr_name, (type_name, value) in custom_attrs.items():
            if not attr_name.startswith("custom:"):
                attr_name = f"custom:{attr_name}"
            Sdf.AttributeSpec(ms, attr_name, type_name).default = value


# ── Batch USD writer ──────────────────────────────────────────────────────────

def _open_or_create_layer(usd_path):
    """
    Return a fresh, empty Sdf.Layer for *usd_path*.

    MUST be called on the Kit main thread — touches the Sdf layer registry.
    Safe to call Clear() here because callers guarantee the layer has already
    been removed from root_layer.subLayerPaths before this function runs.
    """
    existing = Sdf.Layer.Find(usd_path)
    if existing:
        existing.Clear()
        return existing
    if os.path.exists(usd_path):
        try:
            os.remove(usd_path)
        except Exception:
            pass
    return Sdf.Layer.CreateNew(usd_path)


def write_batch_usd(usd_path, safe_name, features, start_idx, color, basename,
                    origin_lat, origin_lon, *,
                    tube_sides=TUBE_SIDES, tube_radius=TUBE_RADIUS,
                    point_size=POINT_SIZE, node_height=NODE_HEIGHT,
                    poly_height=POLY_HEIGHT):
    """
    Write a batch of GeoJSON features to a single USD file.
    Returns the number of mesh prims created.
    """
    out_layer = _open_or_create_layer(usd_path)
    world_spec = Sdf.PrimSpec(out_layer.pseudoRoot, "World", Sdf.SpecifierDef)
    world_spec.typeName = "Xform"
    group_spec = Sdf.PrimSpec(world_spec, safe_name, Sdf.SpecifierDef)
    group_spec.typeName = "Xform"
    out_layer.defaultPrim = "World"

    created = 0
    for local_i, feature in enumerate(features):
        i = start_idx + local_i
        geom = feature.get("geometry")
        if not geom:
            continue
        gtype = geom.get("type", "")
        coords = geom.get("coordinates", [])

        if gtype == "LineString":
            if len(coords) < 2:
                continue
            pts = [lonlat_to_xyz(c[0], c[1], origin_lon, origin_lat,
                                  c[2] if len(c) > 2 else 0.0) for c in coords]
            verts, fc, fi = build_tube(pts, tube_sides, tube_radius)
            if not verts:
                continue
            sdf_mesh(group_spec, f"Line_{i:06d}", verts, fc, fi, color)
            created += 1

        elif gtype == "Point":
            x, y, z = lonlat_to_xyz(coords[0], coords[1], origin_lon, origin_lat,
                                      coords[2] if len(coords) > 2 else 0.0)
            if basename == "nodes":
                verts, fc, fi = build_cylinder(x, y, z, point_size, node_height, tube_sides)
            elif basename == "poi_safegraph":
                # Low-poly spheres for large SafeGraph datasets (performance)
                verts, fc, fi = build_sphere(x, y, z, point_size, 6, 4)
            else:
                verts, fc, fi = build_sphere(x, y, z, point_size, tube_sides)
            sdf_mesh(group_spec, f"Pt_{i:06d}", verts, fc, fi, color)
            created += 1

        elif gtype == "Polygon":
            if not coords or len(coords[0]) < 3:
                continue
            if basename in ("zones_boundary", "boundary"):
                verts, fc, fi = build_polygon_outline(coords[0], origin_lon, origin_lat)
            else:
                verts, fc, fi = build_polygon(coords[0], origin_lon, origin_lat, poly_height)
            if not verts:
                continue
            sdf_mesh(group_spec, f"Poly_{i:06d}", verts, fc, fi, color)
            created += 1

        elif gtype == "MultiPolygon":
            for pi_idx, polygon in enumerate(coords):
                if not polygon or len(polygon[0]) < 3:
                    continue
                if basename in ("zones_boundary", "boundary"):
                    verts, fc, fi = build_polygon_outline(polygon[0], origin_lon, origin_lat)
                else:
                    verts, fc, fi = build_polygon(polygon[0], origin_lon, origin_lat, poly_height)
                if not verts:
                    continue
                sdf_mesh(group_spec, f"MPoly_{i:06d}_{pi_idx}", verts, fc, fi, color)
            created += 1

    out_layer.Save()
    del out_layer
    return created


# ── Stage sublayer helper (MUST run on Kit main thread) ───────────────────────

def apply_sublayers(stage, usd_paths):
    """
    Add *usd_paths* to the stage root layer's subLayerPaths, removing any
    stale entries first.  Must be called on the Kit main thread — never from
    a thread-pool executor.

    Returns the number of paths added.
    """
    root_layer = stage.GetRootLayer()
    added = 0
    for p in usd_paths:
        while p in root_layer.subLayerPaths:
            root_layer.subLayerPaths.remove(p)
        root_layer.subLayerPaths.append(p)
        added += 1
    return added


# ── High-level entry point ────────────────────────────────────────────────────

def convert_network_folder_to_usd(network_folder, origin_lat, origin_lon,
                                   usd_dir, batch_size=8000):
    """
    Convert every .geojson in *network_folder* to USD files under *usd_dir*.

    Safe to call from a thread-pool executor — does NOT touch the USD stage.
    The caller is responsible for calling ``apply_sublayers(stage, paths)``
    on the Kit main thread after this returns.

    Returns (usd_paths: list[str], status_message: str).
    """
    os.makedirs(usd_dir, exist_ok=True)

    geojson_files = sorted(_glob.glob(os.path.join(network_folder, "*.geojson")))
    if not geojson_files:
        return [], "No .geojson files found in Network/"

    carb.log_info(f"[geojson_usd_writer] Converting {len(geojson_files)} GeoJSON files "
                  f"(origin: lat={origin_lat:.4f} lon={origin_lon:.4f})")

    usd_paths = []

    for filepath in geojson_files:
        basename  = os.path.splitext(os.path.basename(filepath))[0]
        safe_name = basename.replace(" ", "_").replace("-", "_").replace(".", "_")
        color     = FILE_COLORS.get(basename, DEFAULT_COLOR)

        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                features = json.load(fh).get("features", [])
        except Exception as exc:
            carb.log_warn(f"[geojson_usd_writer] Cannot read {filepath}: {exc}")
            continue

        if not features:
            continue

        batches = ([(0, features)] if len(features) <= batch_size
                   else [(bi, features[bi: bi + batch_size])
                         for bi in range(0, len(features), batch_size)])

        for start_idx, batch in batches:
            suffix   = f"_part{start_idx // batch_size:02d}" if len(batches) > 1 else ""
            usd_path = os.path.join(usd_dir, f"{safe_name}{suffix}.usd").replace("\\", "/")

            try:
                created = write_batch_usd(
                    usd_path, safe_name, batch, start_idx, color, basename,
                    origin_lat, origin_lon
                )
                carb.log_info(
                    f"[geojson_usd_writer] {os.path.basename(usd_path)}: {created} prims"
                )
                usd_paths.append(usd_path)
            except Exception as exc:
                carb.log_error(f"[geojson_usd_writer] Error writing {usd_path}: {exc}")

            gc.collect()

    n = len(usd_paths)
    return usd_paths, f"3D network loaded — {n} USD sublayer(s) from {usd_dir}"


# ── Zones-specific entry point ────────────────────────────────────────────────

def convert_zones_folder_to_usd(zones_folder, origin_lat, origin_lon,
                                 usd_dir, batch_size=8000):
    """
    Convert zoning output GeoJSON files to USD files under *usd_dir*.

    Safe to call from a thread-pool executor — does NOT touch the USD stage.
    The caller is responsible for calling ``apply_sublayers(stage, paths)``
    on the Kit main thread.

    Special rendering rules:
      • Zones.geojson       → zones_fill.usd  (gold) + zones_boundary.usd (cyan)
      • POI_safegraph.geojson → poi_safegraph.usd (red spheres)
      • Any other .geojson  → standard conversion

    Returns (usd_paths: list[str], status_message: str).
    """
    os.makedirs(usd_dir, exist_ok=True)
    usd_paths = []

    def _write_batches(features, usd_stem, safe_name, color, basename, **kwargs):
        """Write *features* in batches; return list of created USD paths."""
        batches = ([(0, features)] if len(features) <= batch_size
                   else [(bi, features[bi: bi + batch_size])
                         for bi in range(0, len(features), batch_size)])
        paths = []
        for start_idx, batch in batches:
            suffix   = f"_part{start_idx // batch_size:02d}" if len(batches) > 1 else ""
            usd_path = os.path.join(usd_dir, f"{usd_stem}{suffix}.usd").replace("\\", "/")
            try:
                write_batch_usd(usd_path, safe_name, batch, start_idx,
                                color, basename, origin_lat, origin_lon, **kwargs)
                paths.append(usd_path)
            except Exception as exc:
                carb.log_error(f"[geojson_usd_writer] Error writing {usd_path}: {exc}")
        gc.collect()
        return paths

    # ── 1. Zones.geojson → fill + boundary layers ─────────────────────────────
    zones_geojson = os.path.join(zones_folder, "Zones.geojson")
    if os.path.exists(zones_geojson):
        try:
            with open(zones_geojson, "r", encoding="utf-8") as fh:
                zone_features = json.load(fh).get("features", [])
        except Exception as exc:
            carb.log_warn(f"[geojson_usd_writer] Cannot read Zones.geojson: {exc}")
            zone_features = []

        if zone_features:
            carb.log_info(
                f"[geojson_usd_writer] Zones.geojson: {len(zone_features)} features — "
                "writing fill + boundary layers"
            )
            usd_paths += _write_batches(
                zone_features, "zones_fill", "zones_fill",
                FILE_COLORS["zones"], "zones",
                poly_height=ZONE_FILL_HEIGHT,
            )
            usd_paths += _write_batches(
                zone_features, "zones_boundary", "zones_boundary",
                FILE_COLORS["zones_boundary"], "zones_boundary",
            )

    # ── 2. POI_safegraph.geojson → red sphere layer ───────────────────────────
    poi_geojson = os.path.join(zones_folder, "POI_safegraph.geojson")
    if os.path.exists(poi_geojson):
        try:
            with open(poi_geojson, "r", encoding="utf-8") as fh:
                poi_features = json.load(fh).get("features", [])
        except Exception as exc:
            carb.log_warn(f"[geojson_usd_writer] Cannot read POI_safegraph.geojson: {exc}")
            poi_features = []

        if poi_features:
            carb.log_info(
                f"[geojson_usd_writer] POI_safegraph.geojson: {len(poi_features)} features"
            )
            usd_paths += _write_batches(
                poi_features, "poi_safegraph", "poi_safegraph",
                FILE_COLORS["poi_safegraph"], "poi_safegraph",
                point_size=POI_SPHERE_SIZE,
            )

    # ── 3. Any other .geojson files → standard conversion ────────────────────
    special = {"zones.geojson", "poi_safegraph.geojson"}
    for filepath in sorted(_glob.glob(os.path.join(zones_folder, "*.geojson"))):
        if os.path.basename(filepath).lower() in special:
            continue
        basename  = os.path.splitext(os.path.basename(filepath))[0]
        safe_name = basename.replace(" ", "_").replace("-", "_").replace(".", "_")
        color     = FILE_COLORS.get(safe_name, DEFAULT_COLOR)
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                features = json.load(fh).get("features", [])
        except Exception as exc:
            carb.log_warn(f"[geojson_usd_writer] Cannot read {filepath}: {exc}")
            continue
        if not features:
            continue
        usd_paths += _write_batches(features, safe_name, safe_name, color, basename)

    n = len(usd_paths)
    status = (f"Zones loaded — {n} USD sublayer(s): "
              f"zone fills, zone boundaries, SafeGraph POIs")
    carb.log_info(f"[geojson_usd_writer] {status}")
    return usd_paths, status


# ── Noise decay band entry point ──────────────────────────────────────────────

def convert_noise_bands_to_usd(geojson_path, origin_lat, origin_lon,
                                usd_dir, batch_size=4000):
    """
    Convert noise_decay_dissolved.geojson (or noise_decay_bands.geojson) to USD.

    Safe to call from a thread-pool executor — does NOT touch the USD stage.
    The caller is responsible for calling ``apply_sublayers(stage, paths)``
    on the Kit main thread.

    Each distance band → its own colour-coded USD file with decreasing heights:
        0-10 m  → 55 m  deep red  |  10-25 m → 40 m  orange
        25-50 m → 26 m  amber     |  50-100 m → 14 m  yellow-green
        100-200m →  5 m green

    Returns (usd_paths: list[str], status_message: str).
    """
    if not os.path.exists(geojson_path):
        return [], f"Noise GeoJSON not found: {geojson_path}"

    os.makedirs(usd_dir, exist_ok=True)

    try:
        with open(geojson_path, "r", encoding="utf-8") as fh:
            all_features = json.load(fh).get("features", [])
    except Exception as exc:
        return [], f"Cannot read {geojson_path}: {exc}"

    if not all_features:
        return [], "Noise GeoJSON has no features"

    from collections import defaultdict as _dd
    bands = _dd(list)
    for feat in all_features:
        band = feat.get("properties", {}).get("distance_band", "unknown")
        bands[band].append(feat)

    carb.log_info(
        "[geojson_usd_writer] Noise bands: "
        + ", ".join(f"{b}={len(v)}" for b, v in sorted(bands.items()))
    )

    usd_paths  = []
    band_order = ["0-10m", "10-25m", "25-50m", "50-100m", "100-200m"]

    for band in band_order:
        features = bands.get(band, [])
        if not features:
            continue

        color  = NOISE_BAND_COLORS.get(band, DEFAULT_COLOR)
        height = NOISE_BAND_HEIGHTS.get(band, POLY_HEIGHT)
        safe   = band.replace("-", "_").replace(" ", "_")

        batches = ([(0, features)] if len(features) <= batch_size
                   else [(bi, features[bi: bi + batch_size])
                         for bi in range(0, len(features), batch_size)])

        for start_idx, batch in batches:
            suffix   = f"_part{start_idx // batch_size:02d}" if len(batches) > 1 else ""
            usd_path = os.path.join(usd_dir, f"noise_{safe}{suffix}.usd").replace("\\", "/")

            try:
                created = write_batch_usd(
                    usd_path, f"noise_{safe}", batch, start_idx,
                    color, "noise_band",
                    origin_lat, origin_lon,
                    poly_height=height,
                )
                usd_paths.append(usd_path)
                carb.log_info(
                    f"[geojson_usd_writer] noise {band}: "
                    f"{created} prims  h={height}m  → {os.path.basename(usd_path)}"
                )
            except Exception as exc:
                carb.log_error(f"[geojson_usd_writer] noise {band} error: {exc}")

        gc.collect()

    n = len(usd_paths)
    status = f"Noise decay loaded — {n} USD sublayer(s) (5 concentric bands, red→green)"
    carb.log_info(f"[geojson_usd_writer] {status}")
    return usd_paths, status


# ── OSM Buildings ─────────────────────────────────────────────────────────────

# Building type → display colour (warm tones for residential, cool for commercial)
_BTYPE_COLOR = {
    "yes":          Gf.Vec3f(0.85, 0.78, 0.65),   # neutral beige   — generic/unknown
    "residential":  Gf.Vec3f(0.95, 0.82, 0.60),   # warm sandstone
    "apartments":   Gf.Vec3f(0.90, 0.75, 0.55),   # tan
    "house":        Gf.Vec3f(0.98, 0.88, 0.70),   # light ochre
    "commercial":   Gf.Vec3f(0.40, 0.60, 0.90),   # steel blue
    "retail":       Gf.Vec3f(0.50, 0.70, 0.95),   # sky blue
    "office":       Gf.Vec3f(0.35, 0.55, 0.85),   # deep blue
    "industrial":   Gf.Vec3f(0.55, 0.55, 0.55),   # grey
    "warehouse":    Gf.Vec3f(0.50, 0.50, 0.48),   # dark grey
    "school":       Gf.Vec3f(0.30, 0.80, 0.45),   # green
    "university":   Gf.Vec3f(0.25, 0.75, 0.40),   # forest green
    "hospital":     Gf.Vec3f(0.95, 0.25, 0.25),   # red
    "church":       Gf.Vec3f(0.80, 0.70, 0.90),   # lavender
    "public":       Gf.Vec3f(0.70, 0.85, 0.70),   # pale green
    "civic":        Gf.Vec3f(0.65, 0.80, 0.65),
}
_BTYPE_COLOR_DEFAULT = Gf.Vec3f(0.80, 0.75, 0.65)   # fallback beige


def write_buildings_usd(usd_path, features, start_idx,
                        origin_lat, origin_lon):
    """
    Write a batch of building GeoJSON features to a single USD file.

    Each building becomes a named Mesh prim with:
      • Height from 'height_m' property
      • Colour from building_type
      • Custom attributes: osmId, buildingType, heightM, noiseLeq, name

    Returns the number of Mesh prims created.
    """
    out_layer = _open_or_create_layer(usd_path)
    world_spec = Sdf.PrimSpec(out_layer.pseudoRoot, "World", Sdf.SpecifierDef)
    world_spec.typeName = "Xform"
    bldg_spec = Sdf.PrimSpec(world_spec, "Buildings", Sdf.SpecifierDef)
    bldg_spec.typeName = "Xform"
    out_layer.defaultPrim = "World"

    created = 0
    with Sdf.ChangeBlock():
        for local_i, feature in enumerate(features):
            i = start_idx + local_i
            geom  = feature.get("geometry")
            props = feature.get("properties") or {}
            if not geom:
                continue

            gtype  = geom.get("type", "")
            coords = geom.get("coordinates", [])

            height_m  = float(props.get("height_m", 8.0) or 8.0)
            height_m  = max(2.0, min(float(height_m), 300.0))
            btype     = str(props.get("building_type", "yes") or "yes").lower()
            osm_id    = str(props.get("osm_id", "") or "")
            name_val  = str(props.get("name", "") or "")
            noise_leq = float(props.get("noise_leq", 0.0) or 0.0)
            color     = _BTYPE_COLOR.get(btype, _BTYPE_COLOR_DEFAULT)

            custom_attrs = {
                "custom:osmId":        (Sdf.ValueTypeNames.String, osm_id),
                "custom:buildingType": (Sdf.ValueTypeNames.String, btype),
                "custom:heightM":      (Sdf.ValueTypeNames.Float,  float(height_m)),
                "custom:noiseLeq":     (Sdf.ValueTypeNames.Float,  float(noise_leq)),
                "custom:name":         (Sdf.ValueTypeNames.String, name_val),
            }

            rings = []
            if gtype == "Polygon":
                rings = [coords[0]] if coords else []
            elif gtype == "MultiPolygon":
                rings = [poly[0] for poly in coords if poly]

            for ri, ring in enumerate(rings):
                verts, fc, fi = build_polygon(ring, origin_lon, origin_lat,
                                              poly_height=height_m)
                if not verts:
                    continue

                # Wrap each building in an Xform so it is independently
                # selectable and transformable in the Omniverse viewport.
                xf_name = f"Bldg_{i:06d}" if ri == 0 else f"Bldg_{i:06d}_{ri}"
                xf_spec = Sdf.PrimSpec(bldg_spec, xf_name, Sdf.SpecifierDef)
                xf_spec.typeName = "Xform"
                xf_spec.SetInfo("kind", "component")

                # Write custom metadata on the Xform so it is visible in the
                # Stage Properties panel when a building is selected.
                for attr_name, (type_name, value) in custom_attrs.items():
                    if not attr_name.startswith("custom:"):
                        attr_name = f"custom:{attr_name}"
                    Sdf.AttributeSpec(xf_spec, attr_name, type_name).default = value

                # Mesh lives as a child called "mesh" — standard USD practice.
                sdf_mesh(xf_spec, "mesh", verts, fc, fi, color)
                created += 1

    out_layer.Save()
    del out_layer
    return created


def compute_buildings_batch_geometry(features, start_idx, origin_lat, origin_lon):
    """
    Compute polygon geometry for a batch of building GeoJSON features.

    Safe to call from a background thread — uses only pure Python and Gf math,
    no Sdf or stage operations.  Returns a list of
    (xf_name, verts, fc, fi, color, custom_raw) tuples where custom_raw maps
    attribute name → (float or str) value for later Sdf encoding.
    """
    results = []
    for local_i, feature in enumerate(features):
        i     = start_idx + local_i
        geom  = feature.get("geometry")
        props = feature.get("properties") or {}
        if not geom:
            continue
        gtype  = geom.get("type", "")
        coords = geom.get("coordinates", [])

        height_m  = max(2.0, min(float(props.get("height_m", 8.0) or 8.0), 300.0))
        btype     = str(props.get("building_type", "yes") or "yes").lower()
        osm_id    = str(props.get("osm_id", "") or "")
        name_val  = str(props.get("name", "") or "")
        noise_leq = float(props.get("noise_leq", 0.0) or 0.0)
        color     = _BTYPE_COLOR.get(btype, _BTYPE_COLOR_DEFAULT)
        custom_raw = {
            "osmId":        osm_id,
            "buildingType": btype,
            "heightM":      float(height_m),
            "noiseLeq":     float(noise_leq),
            "name":         name_val,
        }

        rings = []
        if gtype == "Polygon":
            rings = [coords[0]] if coords else []
        elif gtype == "MultiPolygon":
            rings = [poly[0] for poly in coords if poly]

        for ri, ring in enumerate(rings):
            verts, fc, fi = build_polygon(ring, origin_lon, origin_lat, poly_height=height_m)
            if not verts:
                continue
            xf_name = f"Bldg_{i:06d}" if ri == 0 else f"Bldg_{i:06d}_{ri}"
            results.append((xf_name, verts, fc, fi, color, custom_raw))
    return results


def write_buildings_usd_from_geometry(usd_path, geom_list):
    """
    Write pre-computed building geometry to a single USD file.

    MUST be called on the Kit main thread — creates and modifies Sdf layers.
    geom_list: list of (xf_name, verts, fc, fi, color, custom_raw) as returned
               by compute_buildings_batch_geometry().
    Returns the number of Mesh prims created.
    """
    out_layer  = _open_or_create_layer(usd_path)
    world_spec = Sdf.PrimSpec(out_layer.pseudoRoot, "World", Sdf.SpecifierDef)
    world_spec.typeName = "Xform"
    bldg_spec  = Sdf.PrimSpec(world_spec, "Buildings", Sdf.SpecifierDef)
    bldg_spec.typeName = "Xform"
    out_layer.defaultPrim = "World"

    _str_t   = Sdf.ValueTypeNames.String
    _float_t = Sdf.ValueTypeNames.Float

    created = 0
    with Sdf.ChangeBlock():
        for xf_name, verts, fc, fi, color, custom_raw in geom_list:
            xf_spec = Sdf.PrimSpec(bldg_spec, xf_name, Sdf.SpecifierDef)
            xf_spec.typeName = "Xform"
            xf_spec.SetInfo("kind", "component")
            for k, v in custom_raw.items():
                t = _float_t if isinstance(v, float) else _str_t
                Sdf.AttributeSpec(xf_spec, f"custom:{k}", t).default = v
            sdf_mesh(xf_spec, "mesh", verts, fc, fi, color)
            created += 1

    out_layer.Save()
    del out_layer
    return created


def convert_buildings_to_usd(buildings_geojson, origin_lat, origin_lon,
                              usd_dir, batch_size=500, max_features=None):
    """
    Convert buildings.geojson to USD files under *usd_dir*.

    Safe to call from a thread-pool executor — does NOT touch the USD stage.
    The caller is responsible for calling ``apply_sublayers(stage, paths)``
    on the Kit main thread.

    Each batch of *batch_size* buildings becomes a separate .usd file.
    *max_features* hard-caps the number of buildings converted regardless of
    what is in the GeoJSON (guards against stale cached files with more features
    than the current max_buildings setting).

    Returns (usd_paths: list[str], status_message: str).
    """
    if not os.path.exists(buildings_geojson):
        return [], f"buildings.geojson not found: {buildings_geojson}"

    os.makedirs(usd_dir, exist_ok=True)

    try:
        with open(buildings_geojson, "r", encoding="utf-8") as fh:
            all_features = json.load(fh).get("features", [])
    except Exception as exc:
        return [], f"Cannot read {buildings_geojson}: {exc}"

    if not all_features:
        return [], "buildings.geojson has no features"

    if max_features and len(all_features) > max_features:
        carb.log_info(
            f"[geojson_usd_writer] Buildings capped: {len(all_features)} → {max_features}"
        )
        all_features = all_features[:max_features]

    carb.log_info(
        f"[geojson_usd_writer] Buildings: {len(all_features)} features, "
        f"origin lat={origin_lat:.4f} lon={origin_lon:.4f}"
    )

    usd_paths   = []
    total_prims = 0

    batches = ([(0, all_features)] if len(all_features) <= batch_size
               else [(bi, all_features[bi: bi + batch_size])
                     for bi in range(0, len(all_features), batch_size)])

    for start_idx, batch in batches:
        suffix   = f"_part{start_idx // batch_size:02d}" if len(batches) > 1 else ""
        usd_path = os.path.join(usd_dir, f"buildings{suffix}.usd").replace("\\", "/")

        try:
            created = write_buildings_usd(usd_path, batch, start_idx,
                                          origin_lat, origin_lon)
            usd_paths.append(usd_path)
            total_prims += created
            carb.log_info(
                f"[geojson_usd_writer] {os.path.basename(usd_path)}: "
                f"{created} building prims"
            )
        except Exception as exc:
            carb.log_error(f"[geojson_usd_writer] buildings error: {exc}")

        gc.collect()

    status = (f"OSM Buildings loaded — {total_prims} building prims in "
              f"{len(usd_paths)} USD sublayer(s)")
    carb.log_info(f"[geojson_usd_writer] {status}")
    return usd_paths, status
