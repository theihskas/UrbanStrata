"""
terrain_ops.py
==============
Terrain loading stubs (MapBox, OSM, USGS, custom heightmap).
These are placeholder implementations kept for future use.
No UI section currently references them.
"""

import carb
import omni.usd
import omni.kit.commands
from pxr import Gf, UsdGeom


def get_city_coordinates_local(city_name):
    """Minimal geocoding for terrain (use network_ops.get_city_coordinates instead)."""
    fallback = {
        "Dresden, Germany":   (51.0504, 13.7373),
        "Munich, Germany":    (48.1351, 11.5820),
        "Berlin, Germany":    (52.5200, 13.4050),
    }
    return fallback.get(city_name, (51.0504, 13.7373))


def load_mapbox_terrain(city_name, update_status):
    try:
        coords = get_city_coordinates_local(city_name)
        update_status("Loading MapBox terrain via OGC extension...")
        try:
            omni.kit.commands.execute("OGCGenerateTile",
                                      latitude=coords[0], longitude=coords[1],
                                      lat_distance=10.0, lon_distance=10.0)
            update_status("MapBox terrain loaded via OGC")
        except Exception:
            _create_terrain_placeholder(coords, "mapbox")
            update_status("OGC not available — terrain placeholder created")
    except Exception as exc:
        update_status(f"Error loading MapBox terrain: {exc}")


def load_osm_terrain(city_name, update_status):
    try:
        coords = get_city_coordinates_local(city_name)
        _create_terrain_placeholder(coords, "osm")
        update_status("OpenStreetMap terrain placeholder created")
    except Exception as exc:
        update_status(f"Error loading OSM terrain: {exc}")


def load_usgs_terrain(city_name, update_status):
    try:
        coords = get_city_coordinates_local(city_name)
        _create_terrain_placeholder(coords, "usgs")
        update_status("USGS terrain placeholder created")
    except Exception as exc:
        update_status(f"Error loading USGS terrain: {exc}")


def load_custom_heightmap(update_status):
    update_status("Custom heightmap: place a heightmap.png in the project folder")


def _create_terrain_placeholder(coords, terrain_type="basic"):
    try:
        stage = omni.usd.get_context().get_stage()
        size = 10_000  # 10 km
        plane = UsdGeom.Mesh.Define(stage, "/World/Terrain")
        plane.CreatePointsAttr([
            Gf.Vec3f(-size / 2, 0, -size / 2),
            Gf.Vec3f( size / 2, 0, -size / 2),
            Gf.Vec3f( size / 2, 0,  size / 2),
            Gf.Vec3f(-size / 2, 0,  size / 2),
        ])
        plane.CreateFaceVertexCountsAttr([4])
        plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        colors = {
            "basic":  (0.4, 0.3, 0.2),
            "osm":    (0.3, 0.5, 0.3),
            "usgs":   (0.5, 0.4, 0.3),
            "mapbox": (0.2, 0.4, 0.2),
        }
        plane.CreateDisplayColorAttr().Set([Gf.Vec3f(*colors.get(terrain_type, colors["basic"]))])
        plane.GetPrim().SetCustomDataByKey("terrain_type", terrain_type)
        plane.GetPrim().SetCustomDataByKey("coordinates", f"{coords[0]},{coords[1]}")
    except Exception as exc:
        carb.log_error(f"[terrain_ops] _create_terrain_placeholder: {exc}")
