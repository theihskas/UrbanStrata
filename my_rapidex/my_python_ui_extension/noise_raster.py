"""
noise_raster.py
===============
Raster-based noise visualization using GeoTIFF texture draping.

Creates continuous noise heatmaps (GeoTIFF) from link_noise.geojson for
texture-draped visualization over terrain. Complements ribbon/shell visualization
with citywide noise overview and smoother transitions.

Requires: rasterio, numpy, scipy (optional for kriging interpolation).
"""

import os
import json
import math
import tempfile
from pathlib import Path

import carb

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    carb.log_warn("[noise_raster] NumPy not available - raster generation will be skipped")

try:
    import rasterio
    from rasterio.transform import from_bounds
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False
    carb.log_warn("[noise_raster] rasterio not available - GeoTIFF generation will be skipped")


# ── Constants ──────────────────────────────────────────────────────────────────

RASTER_RESOLUTION_M = 20.0      # 20m per pixel (adjust for detail vs performance)
RASTER_PADDING_M    = 500.0    # 500m buffer around bbox
AMBIENT_DB          = 45.0      # Skip roads below this threshold


# ── Color mapping ──────────────────────────────────────────────────────────────

def _db_to_rgba(db, db_min=45.0, db_max=75.0):
    """
    Convert noise dB to RGBA pixel for EU spectral convention.
    blue (quiet) → cyan → green → yellow → orange → red (loud).
    
    Returns: (R, G, B, A) as 0-255 integers.
    """
    t = max(0.0, min(1.0, (db - db_min) / max(db_max - db_min, 1.0)))
    alpha = 200  # Semi-transparent
    
    if t < 0.2:
        f = t / 0.2
        r, g, b = int(0 * 255), int((0.2 + f * 0.6) * 255), 255
    elif t < 0.4:
        f = (t - 0.2) / 0.2
        r, g, b = int(0 * 255), 255, int((1.0 - f) * 255)
    elif t < 0.6:
        f = (t - 0.4) / 0.2
        r, g, b = int(f * 255), 255, 0
    elif t < 0.8:
        f = (t - 0.6) / 0.2
        r, g, b = 255, int((1.0 - f * 0.5) * 255), 0
    else:
        f = (t - 0.8) / 0.2
        r, g, b = 255, int((0.5 - f * 0.5) * 255), 0
    
    return (r, g, b, alpha)


def _lonlat_to_meters(lon, lat, origin_lon, origin_lat):
    """Convert WGS-84 lon/lat to local ENU coordinates (metres)."""
    R = 6_378_137.0
    lat_r = math.radians(origin_lat)
    x = (lon - origin_lon) * R * math.cos(lat_r) * math.pi / 180.0
    y = (lat - origin_lat) * R * math.pi / 180.0
    return x, y


async def create_noise_raster_geotiff(link_noise_geojson, output_geotiff, 
                                       origin_lat, origin_lon,
                                       resolution_m=RASTER_RESOLUTION_M,
                                       interpolation_method="linear"):
    """
    Create a GeoTIFF raster heatmap from link_noise.geojson using rasterization.

    Parameters
    ----------
    link_noise_geojson : str
        Path to link_noise.geojson with LineString geometry and noise property.
    output_geotiff : str
        Output path for GeoTIFF file (e.g., "noise_heatmap.tif").
    origin_lat, origin_lon : float
        Geographic ENU projection origin (should match Cesium georeference).
    resolution_m : float
        Raster pixel size in metres (default 20m).
    interpolation_method : str
        Interpolation method: "linear" (default), "nearest", or "cubic".

    Returns
    -------
    (success: bool, message: str, geotiff_path: str or None)
    """
    if not RASTERIO_AVAILABLE or not NUMPY_AVAILABLE:
        msg = "rasterio or NumPy not available"
        carb.log_warn(f"[noise_raster] {msg}")
        return False, msg, None

    if not os.path.exists(link_noise_geojson):
        msg = f"link_noise.geojson not found: {link_noise_geojson}"
        return False, msg, None

    # ── Load GeoJSON and extract bounding box ──────────────────────────────────
    try:
        with open(link_noise_geojson, "r", encoding="utf-8") as fh:
            geojson_data = json.load(fh)
            features = geojson_data.get("features", [])
    except Exception as exc:
        return False, f"Cannot read GeoJSON: {exc}", None

    if not features:
        return False, "No features in GeoJSON", None

    # Compute bbox and noise statistics
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), -float('inf'), -float('inf')
    db_min, db_max = float('inf'), -float('inf')
    road_points = []

    for feat in features:
        geom = feat.get("geometry", {})
        props = feat.get("properties", {})
        
        if geom.get("type") != "LineString":
            continue
        
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        
        db = float(props.get("noise", 0.0) or 0.0)
        if db < AMBIENT_DB:
            continue
        
        db_min = min(db_min, db)
        db_max = max(db_max, db)
        
        # Store centerline point for rasterization
        for lon, lat in coords:
            x, y = _lonlat_to_meters(lon, lat, origin_lon, origin_lat)
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x), max(max_y, y)
            road_points.append((x, y, db))

    if db_min == float('inf'):
        return False, f"No roads above {AMBIENT_DB} dB", None

    # ── Expand bbox with padding ────────────────────────────────────────────────
    min_x -= RASTER_PADDING_M
    min_y -= RASTER_PADDING_M
    max_x += RASTER_PADDING_M
    max_y += RASTER_PADDING_M

    # ── Create raster grid ──────────────────────────────────────────────────────
    width = int((max_x - min_x) / resolution_m) + 1
    height = int((max_y - min_y) / resolution_m) + 1
    raster = np.zeros((height, width, 4), dtype=np.uint8)  # RGBA

    carb.log_info(f"[noise_raster] raster {width}x{height} @ {resolution_m}m/px "
                  f"- {len(road_points)} points, dB {db_min:.1f}-{db_max:.1f}")

    # ── Rasterize points using simple nearest-neighbor (fast, works for lines) ──
    for x, y, db in road_points:
        px = int((x - min_x) / resolution_m)
        py = int((y - min_y) / resolution_m)
        
        if 0 <= px < width and 0 <= py < height:
            # Blend with existing value (for antialiasing effect)
            existing = raster[py, px]
            new_rgba = _db_to_rgba(db, db_min, db_max)
            
            if existing[3] == 0:  # No value yet
                raster[py, px] = new_rgba
            else:
                # Simple average with existing
                raster[py, px] = tuple(
                    int((existing[i] + new_rgba[i]) / 2) 
                    for i in range(4)
                )

    # ── Write GeoTIFF ──────────────────────────────────────────────────────────
    try:
        # Compute geotransform: convert from local ENU back to lon/lat
        pixel_width = resolution_m
        pixel_height = -resolution_m  # negative because y increases downward in raster
        
        # Top-left corner in local coordinates
        ulx = min_x
        uly = max_y
        
        # Create transform: maps pixel coordinates to local ENU
        transform = rasterio.transform.from_bounds(
            min_x, min_y, max_x, max_y, width, height
        )
        
        # Write as 4-band (RGBA) GeoTIFF
        os.makedirs(os.path.dirname(output_geotiff), exist_ok=True)
        
        with rasterio.open(
            output_geotiff,
            'w',
            driver='GTiff',
            height=height,
            width=width,
            count=4,
            dtype=np.uint8,
            transform=transform,
            crs='EPSG:4326',  # WGS-84
        ) as dst:
            # Write each band
            for band in range(1, 5):
                dst.write(raster[:, :, band-1], band)
        
        msg = (f"Raster drape created: {width}x{height}px @ {resolution_m:.0f}m "
               f"- {len(road_points)} points → {output_geotiff}")
        carb.log_info(f"[noise_raster] {msg}")
        return True, msg, output_geotiff
        
    except Exception as exc:
        return False, f"GeoTIFF write failed: {exc}", None


async def apply_noise_raster_overlay(geotiff_path, origin_lat, origin_lon, stage):
    """
    Apply raster noise heatmap as texture overlay in USD stage.
    
    Creates a large quad mesh draped over the terrain with the GeoTIFF
    texture bound as an OmniPBR material for display.

    Parameters
    ----------
    geotiff_path : str
        Path to GeoTIFF file created by create_noise_raster_geotiff().
    origin_lat, origin_lon : float
        Geographic ENU projection origin.
    stage : Usd.Stage
        Target USD stage.

    Returns
    -------
    (success: bool, message: str)
    """
    if not RASTERIO_AVAILABLE:
        return False, "rasterio not available for texture binding"

    if not os.path.exists(geotiff_path):
        return False, f"GeoTIFF not found: {geotiff_path}"

    try:
        from pxr import UsdGeom, UsdShade, Sdf, Gf

        # Convert GeoTIFF → PNG (Omniverse does not render GeoTIFF as a texture)
        png_path = geotiff_path.replace(".tif", "_overlay.png")
        try:
            with rasterio.open(geotiff_path) as src:
                img_data = src.read()   # (bands, height, width)
                h, w = img_data.shape[1], img_data.shape[2]
            with rasterio.open(png_path, 'w', driver='PNG',
                               height=h, width=w,
                               count=img_data.shape[0], dtype='uint8') as dst:
                dst.write(img_data)
            tex_src = png_path
            carb.log_info(f"[noise_raster] converted to PNG: {png_path}")
        except Exception as conv_exc:
            carb.log_warn(f"[noise_raster] PNG conversion failed ({conv_exc}); using GeoTIFF")
            tex_src = geotiff_path

        # Read GeoTIFF bounds to create mesh
        with rasterio.open(geotiff_path) as src:
            bounds = src.bounds
            left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top

        carb.log_info(f"[noise_raster] applying overlay quad {left:.1f}-{right:.1f}, "
                      f"{bottom:.1f}-{top:.1f}")

        # Create mesh quad spanning the raster bounds
        quad_path = "/World/NoiseRasterOverlay"
        if stage.GetPrimAtPath(quad_path).IsValid():
            stage.RemovePrim(quad_path)

        mesh = UsdGeom.Mesh.Define(stage, quad_path)

        # Four corners in ENU coordinates (0.5 m lift avoids z-fighting)
        verts = [
            Gf.Vec3f(float(left),  float(bottom), 0.5),   # SW
            Gf.Vec3f(float(right), float(bottom), 0.5),   # SE
            Gf.Vec3f(float(right), float(top),    0.5),   # NE
            Gf.Vec3f(float(left),  float(top),    0.5),   # NW
        ]
        mesh.CreatePointsAttr(verts)
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateDoubleSidedAttr(True)

        # UV coordinates: map texture corners to quad corners (vertex interpolation)
        uv_api = UsdGeom.PrimvarsAPI(mesh)
        st = uv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                  UsdGeom.Tokens.vertex)
        st.Set([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])

        # OmniPBR material with diffuse_texture (PNG path, forward slashes for USD)
        tex_uri = os.path.abspath(tex_src).replace("\\", "/")
        mat_path = f"{quad_path}/NoiseTextureMat"
        material = UsdShade.Material.Define(stage, mat_path)
        sh = UsdShade.Shader.Define(stage, f"{mat_path}/OmniPBR")
        sh.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
        sh.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
        sh.CreateInput("diffuse_texture",  Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_uri))
        sh.CreateInput("enable_opacity",   Sdf.ValueTypeNames.Bool ).Set(True)
        sh.CreateInput("opacity_texture",  Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_uri))
        sh.CreateInput("opacity_threshold", Sdf.ValueTypeNames.Float).Set(0.0)
        material.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI(mesh).Bind(material)

        msg = (f"Raster overlay applied at {quad_path} — "
               f"{os.path.basename(tex_src)} ({w}×{h}px)")
        carb.log_info(f"[noise_raster] {msg}")
        return True, msg

    except Exception as exc:
        return False, f"Overlay creation failed: {exc}"
