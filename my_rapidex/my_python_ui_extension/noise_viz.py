"""
noise_viz.py
============
Concentric tube shell visualization for traffic noise (CNOSSOS-EU).

Reads link_noise.geojson (produced by noise_ops.py), creates concentric tube
shells around the noisiest roads and applies OmniPBR solid materials —
same material setup as road pipe meshes so shells are always visible.

Shells are rendered doubleSided=True so they are visible from any camera angle.
They are lifted SHELL_Z_OFFSET metres above the terrain origin so they appear
above the Cesium tile surface.

MUST be called on the Kit main thread (uses UsdGeom + UsdShade + Sdf).
"""

import os
import json
import math

import carb
from pxr import Gf, Sdf, UsdGeom, UsdShade  # Sdf kept for AssetPath in material creation

# -- Shapely imports for building-occlusion geometry (optional) ----------------
_SHAPELY_OK = False
try:
    from shapely.geometry import (
        LineString as _SLS, Polygon as _SPoly,
        MultiPolygon as _SMP, Point as _SPt, MultiPoint as _SMPt,
    )
    from shapely.ops import triangulate as _sh_triangulate
    from shapely.strtree import STRtree as _STRtree
    _SHAPELY_OK = True
except ImportError:
    pass

# ── Visual constants ──────────────────────────────────────────────────────────
SHELL_RADII      = [12, 30, 60, 120, 200]   # metres from road centreline
SHELL_SIDES      = 8                         # polygon sides
MAX_POINTS       = 12                        # centreline simplification waypoints
AMBIENT_DB       = 45.0                      # roads below this dB are skipped
MAX_ROADS        = 150                       # top-N noisiest roads (keep low to avoid blocking UI)
PIPE_EMISSIVE    = 0.4                       # matches road pipe emissive intensity
SHELL_Z_OFFSET   = 3.0                       # metres above terrain origin
REF_DISTANCE     = 7.5                       # metres — noise decay reference distance
N_COLOR_BUCKETS  = 8                         # shared material buckets (same pattern as road pipes)

# Maps noise_decay_bands.geojson distance_band labels to SHELL_RADII indices
BAND_TO_SHELL_IDX = {
    "0-10 m":    0,   # 12 m shell
    "10-25 m":   1,   # 30 m shell
    "25-50 m":   2,   # 60 m shell
    "50-100 m":  3,   # 120 m shell
    "100-200 m": 4,   # 200 m shell
}

# Cartographic drape: flat annular bands whose radii match the decay band file
CARTO_BAND_BOUNDS = [0, 10, 25, 50, 100, 200]   # metres; 5 bands between consecutive pairs
CARTO_BAND_LABELS = ["0-10 m", "10-25 m", "25-50 m", "50-100 m", "100-200 m"]
CARTO_BAND_MID    = [5.0, 17.5, 37.5, 75.0, 150.0]  # midpoint per band for CNOSSOS fallback
CARTO_DB_LO       = 45.0   # EU spectral scale lower bound (= AMBIENT_DB)
CARTO_DB_HI       = 75.0   # EU spectral scale upper bound


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _noise_at_distance(source_db, distance):
    if distance <= REF_DISTANCE:
        return source_db
    return source_db - 10.0 * math.log10(max(distance, 0.001) / REF_DISTANCE)


def _noise_color(db, db_lo, db_hi):
    """Red (loud) -> green (quiet), normalised to the actual dataset dB range."""
    span = max(db_hi - db_lo, 1.0)
    t = max(0.0, min(1.0, (db - db_lo) / span))
    if t > 0.75:
        return Gf.Vec3f(0.95, 0.10, 0.05)             # deep red   (loudest)
    elif t > 0.5:
        f = (t - 0.5) / 0.25
        return Gf.Vec3f(1.0, 0.7 - f * 0.6, 0.05)    # orange
    elif t > 0.25:
        f = (t - 0.25) / 0.25
        return Gf.Vec3f(0.6 + f * 0.4, 0.85, 0.10)   # yellow
    else:
        f = t / 0.25
        return Gf.Vec3f(0.20, 0.5 + f * 0.35, 0.20)  # green     (quietest)


def _lonlat_to_xyz(lon, lat, origin_lon, origin_lat, h=0.0):
    R     = 6_378_137.0
    lat_r = math.radians(origin_lat)
    x = (lon - origin_lon) * R * math.cos(lat_r) * math.pi / 180.0
    y = (lat - origin_lat) * R * math.pi / 180.0
    return (x, y, float(h) + SHELL_Z_OFFSET)


def _simplify(pts, max_pts):
    if len(pts) <= max_pts:
        return pts
    step = (len(pts) - 1) / (max_pts - 1)
    return [pts[int(round(i * step))] for i in range(max_pts)]


def _build_shell_tube(points, radius, sides):
    """Swept tube of *sides*-sided cross-section at *radius* around centreline."""
    verts, fc, fi = [], [], []
    n = len(points)
    for pi in range(n):
        cx, cy, cz = points[pi]
        if pi == 0:
            dx, dy, dz = (points[1][0]-cx, points[1][1]-cy, points[1][2]-cz)
        elif pi == n - 1:
            dx, dy, dz = (cx-points[pi-1][0], cy-points[pi-1][1], cz-points[pi-1][2])
        else:
            dx = points[pi+1][0] - points[pi-1][0]
            dy = points[pi+1][1] - points[pi-1][1]
            dz = points[pi+1][2] - points[pi-1][2]
        L = math.sqrt(dx*dx + dy*dy + dz*dz)
        if L > 0.001:
            dx, dy, dz = dx/L, dy/L, dz/L
        else:
            dx, dy, dz = 1.0, 0.0, 0.0
        if abs(dz) < 0.9:
            rx, ry, rz = -dy, dx, 0.0
        else:
            rx, ry, rz = 1.0, 0.0, 0.0
        rl = math.sqrt(rx*rx + ry*ry + rz*rz)
        if rl > 0.001:
            rx, ry, rz = rx/rl, ry/rl, rz/rl
        ux = dy*rz - dz*ry
        uy = dz*rx - dx*rz
        uz = dx*ry - dy*rx
        for s in range(sides):
            a  = 2.0 * math.pi * s / sides
            ca, sa = math.cos(a), math.sin(a)
            verts.append(Gf.Vec3f(
                cx + radius * (rx*ca + ux*sa),
                cy + radius * (ry*ca + uy*sa),
                cz + radius * (rz*ca + uz*sa),
            ))
    for pi in range(n - 1):
        for s in range(sides):
            ns   = (s + 1) % sides
            v0   = pi*sides + s
            v1   = pi*sides + ns
            v2   = (pi+1)*sides + ns
            v3   = (pi+1)*sides + s
            fc.extend([3, 3])
            # CCW winding when viewed from outside → outward-facing normals so
            # doubleSided=False renders the outer face (not the inner one).
            fi.extend([v0, v2, v1, v0, v3, v2])
    return verts, fc, fi


def _make_shell_material(stage, mat_path, color, opacity=1.0):
    """OmniPBR material for noise shells — solid by default, supports opacity < 1."""
    mat = UsdShade.Material.Get(stage, mat_path)
    if not (mat and mat.GetPrim().IsValid()):
        mat = UsdShade.Material.Define(stage, mat_path)
    shader_path = f"{mat_path}/Shader"
    if stage.GetPrimAtPath(shader_path):
        stage.RemovePrim(shader_path)
    sh = UsdShade.Shader.Define(stage, shader_path)
    sh.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
    sh.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
    sh.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(color)
    sh.CreateInput("emissive_color",         Sdf.ValueTypeNames.Color3f).Set(color)
    sh.CreateInput("emissive_intensity",     Sdf.ValueTypeNames.Float  ).Set(PIPE_EMISSIVE)
    # opacity_threshold=0.0 disables OmniPBR cutout mode so any opacity > 0 renders something
    sh.CreateInput("enable_opacity",         Sdf.ValueTypeNames.Bool   ).Set(True)
    sh.CreateInput("opacity_constant",       Sdf.ValueTypeNames.Float  ).Set(float(opacity))
    sh.CreateInput("opacity_threshold",      Sdf.ValueTypeNames.Float  ).Set(0.0)
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


def update_shell_opacity(stage, opacity):
    """Update opacity of every noise shell material under /World/NoiseDecay/NoiseMaterials."""
    mats_prim = stage.GetPrimAtPath("/World/NoiseDecay/NoiseMaterials")
    if not (mats_prim and mats_prim.IsValid()):
        carb.log_warn("[noise_viz] update_shell_opacity: /World/NoiseDecay/NoiseMaterials not found")
        return
    opacity = float(max(0.0, min(1.0, opacity)))
    for mat_prim in mats_prim.GetChildren():
        sh_prim = mat_prim.GetChild("Shader")
        if not sh_prim.IsValid():
            continue
        sh = UsdShade.Shader(sh_prim)
        inp = sh.GetInput("opacity_constant")
        if inp:
            inp.Set(opacity)
        else:
            sh.CreateInput("opacity_constant", Sdf.ValueTypeNames.Float).Set(opacity)


# ── Public entry points ───────────────────────────────────────────────────────


def _noise_color_spectral(db, db_lo=45.0, db_hi=75.0):
    """
    Continuous spectral colormap matching EU environmental noise map conventions:
    blue (quiet) → cyan → green → yellow → orange → red (loud).
    """
    t = max(0.0, min(1.0, (db - db_lo) / max(db_hi - db_lo, 1.0)))
    if t < 0.2:
        f = t / 0.2
        return Gf.Vec3f(0.0, f * 0.8, 1.0)
    elif t < 0.4:
        f = (t - 0.2) / 0.2
        return Gf.Vec3f(0.0, 0.8 + f * 0.2, 1.0 - f)
    elif t < 0.6:
        f = (t - 0.4) / 0.2
        return Gf.Vec3f(f, 1.0, 0.0)
    elif t < 0.8:
        f = (t - 0.6) / 0.2
        return Gf.Vec3f(1.0, 1.0 - f * 0.5, 0.0)
    else:
        f = (t - 0.8) / 0.2
        return Gf.Vec3f(1.0, 0.5 - f * 0.5, 0.0)


def _build_noise_ribbon(centerline, width=8.0):
    """
    Flat ribbon draped on the road surface.
    Z follows each centerline point (SHELL_Z_OFFSET already embedded) plus a
    0.5 m lift to avoid z-fighting with the terrain tile.
    """
    verts, fc, fi = [], [], []
    n = len(centerline)
    for pi in range(n):
        cx, cy, cz = centerline[pi][0], centerline[pi][1], centerline[pi][2] + 0.5
        if pi == 0:
            dx, dy = centerline[1][0] - cx, centerline[1][1] - cy
        elif pi == n - 1:
            dx, dy = cx - centerline[pi-1][0], cy - centerline[pi-1][1]
        else:
            dx = centerline[pi+1][0] - centerline[pi-1][0]
            dy = centerline[pi+1][1] - centerline[pi-1][1]
        L = math.sqrt(dx*dx + dy*dy)
        if L > 0.001:
            dx, dy = dx/L, dy/L
        rx, ry = dy, -dx
        w = width * 0.5
        verts.append(Gf.Vec3f(cx + rx*w, cy + ry*w, cz))
        verts.append(Gf.Vec3f(cx - rx*w, cy - ry*w, cz))
    for pi in range(n - 1):
        v0, v1 = pi*2, pi*2+1
        v2, v3 = (pi+1)*2, (pi+1)*2+1
        fc.extend([3, 3])
        fi.extend([v0, v2, v1,   v1, v2, v3])
    return verts, fc, fi


def _build_annular_ribbon(centerline, inner_r, outer_r, z_lift=0.5):
    """
    Flat annular strip between inner_r and outer_r on each side of the centerline.
    Produces two parallel strips (left side / right side of road) that together
    form a band from -outer_r to -inner_r and +inner_r to +outer_r.
    For inner_r=0 the two strips merge into a single filled ribbon.

    z_lift: metres above each centerline point — use a unique value per band so
    adjacent bands do not z-fight where their edges coincide.
    """
    verts, fc, fi = [], [], []
    n = len(centerline)
    for pi in range(n):
        cx, cy, cz = centerline[pi][0], centerline[pi][1], centerline[pi][2] + z_lift
        if pi == 0:
            dx, dy = centerline[1][0] - cx, centerline[1][1] - cy
        elif pi == n - 1:
            dx, dy = cx - centerline[pi-1][0], cy - centerline[pi-1][1]
        else:
            dx = centerline[pi+1][0] - centerline[pi-1][0]
            dy = centerline[pi+1][1] - centerline[pi-1][1]
        L = math.sqrt(dx*dx + dy*dy)
        if L > 0.001:
            dx, dy = dx/L, dy/L
        rx, ry = dy, -dx   # perpendicular (right side of road)
        # 4 verts per point: right_outer, right_inner, left_inner, left_outer
        verts.append(Gf.Vec3f(cx + rx*outer_r, cy + ry*outer_r, cz))  # 0 right outer
        verts.append(Gf.Vec3f(cx + rx*inner_r, cy + ry*inner_r, cz))  # 1 right inner
        verts.append(Gf.Vec3f(cx - rx*inner_r, cy - ry*inner_r, cz))  # 2 left inner
        verts.append(Gf.Vec3f(cx - rx*outer_r, cy - ry*outer_r, cz))  # 3 left outer
    for pi in range(n - 1):
        b, bn = pi * 4, (pi + 1) * 4
        ro,  ri,  li,  lo  = b,    b+1,  b+2,  b+3
        ro1, ri1, li1, lo1 = bn,   bn+1, bn+2, bn+3
        # right strip
        fc.extend([3, 3])
        fi.extend([ro, ri, ri1,   ro, ri1, ro1])
        # left strip
        fc.extend([3, 3])
        fi.extend([li, lo, lo1,   li, lo1, li1])
    return verts, fc, fi


async def apply_noise_cartographic(link_noise_geojson, decay_bands_geojson,
                                    origin_lat, origin_lon, stage):
    """
    Cartographic noise drape with 5 concentric annular bands per road.

    Each road above AMBIENT_DB gets one flat mesh per distance band
    (0-10 m, 10-25 m, 25-50 m, 50-100 m, 100-200 m).  Band widths match
    the noise_decay_bands.geojson distance bands exactly.  Color follows the
    EU spectral convention (blue=quiet → red=loud) using each band's noise_d
    value; bands that fall below AMBIENT_DB are omitted.

    Parameters
    ----------
    link_noise_geojson : str   — geometry + per-link noise (dB) reference values
    decay_bands_geojson : str  — per-link per-band noise_d values
    origin_lat/lon : float     — ENU projection origin
    stage : Usd.Stage

    Returns (success: bool, message: str).
    """
    for path, label in [(link_noise_geojson, "link_noise.geojson"),
                        (decay_bands_geojson, "noise_decay_bands.geojson")]:
        if not os.path.exists(path):
            return False, f"{label} not found: {path}"

    # ── Load decay-band lookup table ──────────────────────────────────────────
    try:
        with open(decay_bands_geojson, "r", encoding="utf-8") as fh:
            bands_features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read decay_bands: {exc}"

    # decay_map: (from_str, to_str) -> {band_label: noise_d}
    decay_map = {}
    for feat in bands_features:
        props = feat.get("properties", {})
        try:
            key = (str(int(float(props["from"]))), str(int(float(props["to"]))))
        except (KeyError, ValueError, TypeError):
            continue
        band    = str(props.get("distance_band", "")).strip()
        noise_d = float(props.get("noise_d", 0.0) or 0.0)
        decay_map.setdefault(key, {})[band] = noise_d

    # ── Load link geometry + reference noise ──────────────────────────────────
    try:
        with open(link_noise_geojson, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read link_noise: {exc}"

    edge_data = []
    for feat in features:
        geom  = feat.get("geometry", {})
        props = feat.get("properties", {})
        if not geom or geom.get("type") != "LineString":
            continue
        if len(geom.get("coordinates", [])) < 2:
            continue
        try:
            key = (str(int(float(props["from"]))), str(int(float(props["to"]))))
        except (KeyError, ValueError, TypeError):
            key = None
        db = float(props.get("noise", 0.0) or 0.0)
        if db >= AMBIENT_DB:
            edge_data.append((feat, key, db))

    if not edge_data:
        return False, f"No roads above {AMBIENT_DB} dB in link_noise.geojson"

    edge_data.sort(key=lambda x: -x[2])
    db_max = edge_data[0][2]
    carb.log_info(f"[noise_viz] cartographic drape: {len(edge_data)} roads  "
                  f"dB max {db_max:.1f}")

    # ── Build USD scene ────────────────────────────────────────────────────────
    group_path = "/World/NoiseCartographic"
    if stage.GetPrimAtPath(group_path).IsValid():
        stage.RemovePrim(group_path)
    UsdGeom.Xform.Define(stage, group_path)

    import omni.kit.app as _kit_app
    mats_path = f"{group_path}/CartoMaterials"
    UsdGeom.Xform.Define(stage, mats_path)
    # One material per (band_idx, color_bucket); at most 5×16=80 total
    CARTO_BUCKETS = 16
    bucket_mats   = {}   # (band_idx, bucket) -> UsdShade.Material
    total_meshes  = 0

    for feat_idx, (feat, link_key, noise_ref) in enumerate(edge_data):
        coords     = feat["geometry"]["coordinates"]
        centerline = [_lonlat_to_xyz(c[0], c[1], origin_lon, origin_lat,
                                     c[2] if len(c) > 2 else 0.0)
                      for c in coords]
        centerline = _simplify(centerline, MAX_POINTS)
        if len(centerline) < 2:
            continue

        road_group = f"{group_path}/Road_{feat_idx:05d}"
        UsdGeom.Xform.Define(stage, road_group)
        link_bands = decay_map.get(link_key, {}) if link_key else {}

        for band_idx, label in enumerate(CARTO_BAND_LABELS):
            inner_r = float(CARTO_BAND_BOUNDS[band_idx])
            outer_r = float(CARTO_BAND_BOUNDS[band_idx + 1])

            # CNOSSOS gives physically correct monotonic decay per band.
            # Use the map value only when it's strictly lower (measured data,
            # e.g. noise barrier), so bands always grow quieter outward.
            db_cnossos = _noise_at_distance(noise_ref, CARTO_BAND_MID[band_idx])
            db_map     = float(link_bands.get(label) or 0)
            db_band    = min(db_cnossos, db_map) if db_map > AMBIENT_DB else db_cnossos

            if db_band < AMBIENT_DB:
                break   # outer bands will be even quieter

            # Fixed EU spectral scale (45–75 dB) so colors are absolute and
            # comparable across roads, not compressed to the dataset's dB range.
            color  = _noise_color_spectral(db_band, db_lo=CARTO_DB_LO, db_hi=CARTO_DB_HI)
            t      = max(0.0, min(1.0, (db_band - CARTO_DB_LO) / (CARTO_DB_HI - CARTO_DB_LO)))
            bucket = min(CARTO_BUCKETS - 1, int(t * CARTO_BUCKETS))
            mat_key = (band_idx, bucket)
            if mat_key not in bucket_mats:
                mat_path = f"{mats_path}/Band{band_idx}_Bucket{bucket}"
                bucket_mats[mat_key] = _make_shell_material(stage, mat_path, color)

            # Stagger each band 5 cm higher than the one inside it so adjacent
            # band edges and overlapping zones from different roads do not z-fight.
            verts, fc, fi = _build_annular_ribbon(
                centerline, inner_r, outer_r, z_lift=0.5 + band_idx * 0.05)
            if not verts:
                continue

            mesh = UsdGeom.Mesh.Define(stage, f"{road_group}/Band_{band_idx}")
            mesh.CreatePointsAttr(verts)
            mesh.CreateFaceVertexCountsAttr(fc)
            mesh.CreateFaceVertexIndicesAttr(fi)
            mesh.CreateSubdivisionSchemeAttr("none")
            mesh.CreateDoubleSidedAttr(True)
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(bucket_mats[mat_key])
            total_meshes += 1
            _p = mesh.GetPrim()
            _p.CreateAttribute("custom:noise_db",      Sdf.ValueTypeNames.Float ).Set(float(db_band))
            _p.CreateAttribute("custom:distance_band", Sdf.ValueTypeNames.String).Set(label)
            _p.CreateAttribute("custom:source_db",     Sdf.ValueTypeNames.Float ).Set(float(noise_ref))
            _p.CreateAttribute("custom:road_idx",      Sdf.ValueTypeNames.Int   ).Set(feat_idx)

        if feat_idx % 20 == 19:
            carb.log_info(f"[noise_viz] cartographic: {feat_idx+1}/{len(edge_data)} roads "
                          f"({total_meshes} meshes)...")
            await _kit_app.get_app().next_update_async()

    msg = (f"Cartographic drape: {len(edge_data)} roads | {total_meshes} band meshes "
           f"at /World/NoiseCartographic — dB max {db_max:.0f}")
    carb.log_info(f"[noise_viz] {msg}")
    return True, msg


async def apply_noise_shell_from_precomputed(decay_bands_geojson, link_noise_geojson,
                                             origin_lat, origin_lon, output_dir, stage,
                                             opacity=1.0):
    """
    Build concentric shell visualization from pre-computed noise_decay_bands.geojson.

    Parameters
    ----------
    decay_bands_geojson : str
        Path to noise_decay_bands.geojson with columns: from, to, distance_band, noise_d.
        Each link appears 5 times — one row per distance band (0-10 m … 100-200 m).
    link_noise_geojson : str
        Path to link_noise.geojson with LineString geometry and overall noise per link.
    origin_lat / origin_lon : float
        Geographic ENU projection origin (must match Cesium georeference).
    output_dir : str
        Kept for API compatibility.
    stage : Usd.Stage
        Active Isaac Sim stage. Must be called on Kit main thread.
    opacity : float
        Initial shell material opacity (0.0 = transparent, 1.0 = solid).

    Returns
    -------
    (success: bool, message: str)
    """
    for path, label in [(decay_bands_geojson, "noise_decay_bands.geojson"),
                        (link_noise_geojson,  "link_noise.geojson")]:
        if not os.path.exists(path):
            return False, f"{label} not found: {path}"

    # ── Load pre-computed decay bands ─────────────────────────────────────────
    try:
        with open(decay_bands_geojson, "r", encoding="utf-8") as fh:
            bands_features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read decay_bands: {exc}"

    # decay_map: (from_str, to_str) -> {distance_band_label: noise_d}
    decay_map = {}
    for feat in bands_features:
        props = feat.get("properties", {})
        try:
            key = (str(int(float(props["from"]))), str(int(float(props["to"]))))
        except (KeyError, ValueError, TypeError):
            continue
        band   = str(props.get("distance_band", "")).strip()
        noise_d = float(props.get("noise_d", 0.0) or 0.0)
        decay_map.setdefault(key, {})[band] = noise_d

    # ── Load link geometry + reference noise ─────────────────────────────────
    try:
        with open(link_noise_geojson, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read link_noise: {exc}"

    edge_data = []
    for feat in features:
        geom  = feat.get("geometry", {})
        props = feat.get("properties", {})
        if not geom or geom.get("type") != "LineString":
            continue
        if len(geom.get("coordinates", [])) < 2:
            continue
        try:
            key = (str(int(float(props["from"]))), str(int(float(props["to"]))))
        except (KeyError, ValueError, TypeError):
            continue
        noise_ref = float(props.get("noise", 0.0) or 0.0)
        if noise_ref < AMBIENT_DB:
            continue
        edge_data.append((feat, key, noise_ref))

    edge_data.sort(key=lambda x: -x[2])
    if len(edge_data) > MAX_ROADS:
        edge_data = edge_data[:MAX_ROADS]

    if not edge_data:
        return False, (f"No roads above {AMBIENT_DB} dB in {link_noise_geojson} -- "
                       "check that the noise column is populated")

    db_min = edge_data[-1][2]
    db_max = edge_data[0][2]
    carb.log_info(f"[noise_viz] pre-computed: {len(edge_data)} roads  "
                  f"dB {db_min:.1f}-{db_max:.1f}")

    # ── Clear previous visualization ──────────────────────────────────────────
    group_path = "/World/NoiseDecay"
    if stage.GetPrimAtPath(group_path).IsValid():
        stage.RemovePrim(group_path)
    UsdGeom.Xform.Define(stage, group_path)

    import omni.kit.app as _kit_app
    mats_path = f"{group_path}/NoiseMaterials"
    UsdGeom.Xform.Define(stage, mats_path)
    bucket_mats = {}
    total_shells = 0

    band_labels_ordered = ["0-10 m", "10-25 m", "25-50 m", "50-100 m", "100-200 m"]

    for feat_idx, (feat, link_key, noise_ref) in enumerate(edge_data):
        coords     = feat["geometry"]["coordinates"]
        centerline = [_lonlat_to_xyz(c[0], c[1], origin_lon, origin_lat,
                                     c[2] if len(c) > 2 else 0.0)
                      for c in coords]
        centerline = _simplify(centerline, MAX_POINTS)
        if len(centerline) < 2:
            continue

        road_group = f"{group_path}/Road_{feat_idx:05d}"
        UsdGeom.Xform.Define(stage, road_group)

        link_bands = decay_map.get(link_key, {})

        for shell_idx, radius in enumerate(SHELL_RADII):
            band_label = band_labels_ordered[shell_idx]
            # Use pre-computed value when available; fall back to CNOSSOS calculation
            if link_bands and band_label in link_bands:
                db_at_shell = link_bands[band_label]
            else:
                db_at_shell = _noise_at_distance(noise_ref, radius)

            if db_at_shell < AMBIENT_DB:
                break

            db_lo  = AMBIENT_DB
            db_hi  = db_max
            color  = _noise_color(db_at_shell, db_lo, db_hi)
            t      = max(0.0, min(1.0, (db_at_shell - db_lo) / max(db_hi - db_lo, 1.0)))
            bucket = min(N_COLOR_BUCKETS - 1, int(t * N_COLOR_BUCKETS))
            key    = (shell_idx, bucket)
            if key not in bucket_mats:
                mat_path = f"{mats_path}/Shell{shell_idx}_Bucket{bucket}"
                bucket_mats[key] = _make_shell_material(stage, mat_path, color, opacity)

            verts, fc, fi = _build_shell_tube(centerline, radius, SHELL_SIDES)
            if not verts:
                continue

            shell_path = f"{road_group}/Shell_{shell_idx}"
            mesh = UsdGeom.Mesh.Define(stage, shell_path)
            mesh.CreatePointsAttr(verts)
            mesh.CreateFaceVertexCountsAttr(fc)
            mesh.CreateFaceVertexIndicesAttr(fi)
            mesh.CreateSubdivisionSchemeAttr("none")
            mesh.CreateDoubleSidedAttr(True)
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(bucket_mats[key])
            total_shells += 1
            _p = mesh.GetPrim()
            _p.CreateAttribute("custom:noise_db",      Sdf.ValueTypeNames.Float ).Set(float(db_at_shell))
            _p.CreateAttribute("custom:distance_band", Sdf.ValueTypeNames.String).Set(band_label)
            _p.CreateAttribute("custom:source_db",     Sdf.ValueTypeNames.Float ).Set(float(noise_ref))
            _p.CreateAttribute("custom:road_idx",      Sdf.ValueTypeNames.Int   ).Set(feat_idx)

        if feat_idx % 20 == 19:
            carb.log_info(f"[noise_viz] {feat_idx+1}/{len(edge_data)} roads "
                          f"({total_shells} shells so far)...")
            await _kit_app.get_app().next_update_async()

    msg = (f"{len(edge_data)} roads | {total_shells} shells at "
           f"/World/NoiseDecay - dB range {db_min:.0f}-{db_max:.0f}")
    carb.log_info(f"[noise_viz] {msg}")
    return True, msg

async def apply_noise_shell_visualization(link_noise_geojson, origin_lat, origin_lon,
                                          output_dir, stage):
    """
    Build Pallavi-style concentric shell visualization on *stage*.

    Parameters
    ----------
    link_noise_geojson : str
        Path to link_noise.geojson produced by noise_ops.compute_noise_decay().
        Each feature must be a LineString with a 'noise' property (Leq dB).
    origin_lat / origin_lon : float
        Geographic origin for local ENU projection (should match Cesium
        georeference origin set when the network was loaded).
    output_dir : str
        Output directory (kept for API compatibility).
    stage : Usd.Stage
        Active Isaac Sim stage. MUST be called on the Kit main thread.

    Returns
    -------
    (success: bool, message: str)
    """
    if not os.path.exists(link_noise_geojson):
        return False, f"link_noise.geojson not found: {link_noise_geojson}"

    # ── Load + rank ───────────────────────────────────────────────────────────
    try:
        with open(link_noise_geojson, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read {link_noise_geojson}: {exc}"

    edge_noise = []
    for feat in features:
        geom  = feat.get("geometry", {})
        props = feat.get("properties", {})
        if not geom or geom.get("type") != "LineString":
            continue
        if len(geom.get("coordinates", [])) < 2:
            continue
        db = float(props.get("noise", 0.0) or 0.0)
        if db >= AMBIENT_DB:
            edge_noise.append((feat, db))

    edge_noise.sort(key=lambda x: -x[1])
    if len(edge_noise) > MAX_ROADS:
        edge_noise = edge_noise[:MAX_ROADS]

    if not edge_noise:
        return False, (f"No roads above {AMBIENT_DB} dB in link_noise.geojson — "
                       "check that noise computation completed successfully")

    db_min = edge_noise[-1][1]
    db_max = edge_noise[0][1]
    carb.log_info(f"[noise_viz] {len(edge_noise)} roads  "
                  f"dB range {db_min:.1f} – {db_max:.1f}")

    # ── Clear previous visualization ──────────────────────────────────────────
    group_path = "/World/NoiseDecay"
    if stage.GetPrimAtPath(group_path).IsValid():
        stage.RemovePrim(group_path)
    UsdGeom.Xform.Define(stage, group_path)

    # ── Pre-build shared bucket materials (same pattern as road pipe meshes) ──
    # Each (shell_idx, color_bucket) pair gets one shared material so we create
    # at most len(SHELL_RADII) × N_COLOR_BUCKETS = 40 materials total instead
    # of one per mesh prim.
    import omni.kit.app as _kit_app
    mats_path = f"{group_path}/NoiseMaterials"
    UsdGeom.Xform.Define(stage, mats_path)
    bucket_mats = {}   # (shell_idx, bucket) -> UsdShade.Material

    total_shells = 0
    _shell_band_labels = ["0-10 m", "10-25 m", "25-50 m", "50-100 m", "100-200 m"]

    for feat_idx, (feat, source_db) in enumerate(edge_noise):
        coords     = feat["geometry"]["coordinates"]
        centerline = [_lonlat_to_xyz(c[0], c[1], origin_lon, origin_lat,
                                     c[2] if len(c) > 2 else 0.0)
                      for c in coords]
        centerline = _simplify(centerline, MAX_POINTS)
        if len(centerline) < 2:
            continue

        road_group = f"{group_path}/Road_{feat_idx:05d}"
        UsdGeom.Xform.Define(stage, road_group)

        for shell_idx, radius in enumerate(SHELL_RADII):
            db_at_shell = _noise_at_distance(source_db, radius)
            if db_at_shell < AMBIENT_DB:
                break

            # Normalise to dataset range: innermost shell of loudest road = red,
            # outermost shell decaying toward ambient = green.
            db_lo  = AMBIENT_DB
            db_hi  = db_max
            color  = _noise_color(db_at_shell, db_lo, db_hi)
            t      = max(0.0, min(1.0, (db_at_shell - db_lo) / max(db_hi - db_lo, 1.0)))
            bucket = min(N_COLOR_BUCKETS - 1, int(t * N_COLOR_BUCKETS))
            key    = (shell_idx, bucket)
            if key not in bucket_mats:
                mat_path = f"{mats_path}/Shell{shell_idx}_Bucket{bucket}"
                bucket_mats[key] = _make_shell_material(stage, mat_path, color)

            verts, fc, fi = _build_shell_tube(centerline, radius, SHELL_SIDES)
            if not verts:
                continue

            shell_path = f"{road_group}/Shell_{shell_idx}"
            mesh = UsdGeom.Mesh.Define(stage, shell_path)
            mesh.CreatePointsAttr(verts)
            mesh.CreateFaceVertexCountsAttr(fc)
            mesh.CreateFaceVertexIndicesAttr(fi)
            mesh.CreateSubdivisionSchemeAttr("none")
            mesh.CreateDoubleSidedAttr(True)
            UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(bucket_mats[key])
            total_shells += 1
            _p = mesh.GetPrim()
            _p.CreateAttribute("custom:noise_db",      Sdf.ValueTypeNames.Float ).Set(float(db_at_shell))
            _p.CreateAttribute("custom:distance_band", Sdf.ValueTypeNames.String).Set(_shell_band_labels[shell_idx])
            _p.CreateAttribute("custom:source_db",     Sdf.ValueTypeNames.Float ).Set(float(source_db))
            _p.CreateAttribute("custom:road_idx",      Sdf.ValueTypeNames.Int   ).Set(feat_idx)

        # Yield to the Kit event loop every 20 roads so the UI stays responsive
        if feat_idx % 20 == 19:
            carb.log_info(f"[noise_viz] {feat_idx+1}/{len(edge_noise)} roads "
                          f"({total_shells} shells so far)…")
            await _kit_app.get_app().next_update_async()

    msg = (f"{len(edge_noise)} roads | {total_shells} shells visible at "
           f"/World/NoiseDecay - dB range {db_min:.0f}-{db_max:.0f}")
    carb.log_info(f"[noise_viz] {msg}")
    return True, msg