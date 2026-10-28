"""
building_ops.py
===============
Download OSM building footprints for a city, optionally join noise data,
and save as buildings.geojson ready for USD conversion.

All heavy work (osmnx download, spatial join) runs in a subprocess so
Isaac Sim is never blocked.
"""

import os
import carb

_EXT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
).replace("\\", "/")

_RAPIDXPY_CONDA_ENV = "C:/users/sakshi/miniforge3/envs/rapidxpy"
_PYTHON_CANDIDATES = [
    f"{_RAPIDXPY_CONDA_ENV}/python.exe",
    "C:/isaac-sim/kit/python/python.exe",
    "C:/isaac-sim/python/python.exe",
]

# Building types to skip by default (tiny/irrelevant structures)
_SKIP_TYPES = {"shed", "garage", "garages", "carport", "roof",
               "hut", "outhouse", "container", "kiosk"}


def _find_python():
    import shutil
    for c in _PYTHON_CANDIDATES:
        if os.path.isfile(c):
            return c.replace("\\", "/")
    return shutil.which("python") or "python"


def download_buildings(city_name, output_dir, noise_geojson=None, max_buildings=5000):
    """
    Download OSM building footprints for *city_name* in a subprocess.

    Parameters
    ----------
    city_name      : str       e.g. "Dresden, Germany"
    output_dir     : str       Directory to write buildings.geojson
    noise_geojson  : str|None  Path to noise_decay_dissolved.geojson.
                               If provided, each building gets a noise_leq
                               attribute from a spatial join.
    max_buildings  : int       Cap — prevents stage overload for large cities.

    Returns
    -------
    (success: bool, message: str, geojson_path: str|None)
    """
    import subprocess as _sp
    import tempfile

    output_dir   = output_dir.replace("\\", "/")
    out_geojson  = os.path.join(output_dir, "buildings.geojson").replace("\\", "/")
    noise_arg    = (noise_geojson or "").replace("\\", "/")
    python_exe   = _find_python()

    skip_repr = repr(_SKIP_TYPES)

    script = f"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, {_EXT_ROOT!r})

import osmnx as ox
import geopandas as gpd
import pandas as pd

os.makedirs({output_dir!r}, exist_ok=True)

print(f"[buildings] Downloading footprints for {city_name!r} ...")
try:
    bdf = ox.features_from_place({city_name!r}, tags={{'building': True}})
except Exception as e:
    raise RuntimeError(f"osmnx download failed: {{e}}")

print(f"[buildings] Raw features: {{len(bdf)}}")

# Keep only polygon geometries
bdf = bdf[bdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
print(f"[buildings] After polygon filter: {{len(bdf)}}")

# Reset index so osmid becomes a column
bdf = bdf.reset_index()
if 'osmid' in bdf.columns:
    bdf['osm_id'] = bdf['osmid'].astype(str)
else:
    bdf['osm_id'] = bdf.index.astype(str)

# ── Normalise building type ───────────────────────────────────────────────────
if 'building' in bdf.columns:
    bdf['building_type'] = bdf['building'].fillna('yes').astype(str).str.lower()
else:
    bdf['building_type'] = 'yes'

# Filter out trivial structures
skip_types = {skip_repr}
bdf = bdf[~bdf['building_type'].isin(skip_types)].copy()
print(f"[buildings] After type filter: {{len(bdf)}}")

# Cap total count
if len(bdf) > {max_buildings}:
    # Prefer larger / named buildings
    if 'name' in bdf.columns:
        named   = bdf[bdf['name'].notna()].copy()
        unnamed = bdf[bdf['name'].isna()].copy()
        bdf = pd.concat([named, unnamed]).head({max_buildings})
    else:
        bdf = bdf.head({max_buildings})
    print(f"[buildings] Capped to {{len(bdf)}} buildings")

# ── Height ───────────────────────────────────────────────────────────────────
def _height(row):
    if 'height' in row.index:
        h = row.get('height')
        if pd.notna(h):
            try:
                return float(str(h).replace('m','').strip())
            except Exception:
                pass
    if 'building:levels' in row.index:
        lv = row.get('building:levels')
        if pd.notna(lv):
            try:
                return float(lv) * 3.5
            except Exception:
                pass
    # Estimate by type
    defaults = {{'apartments':18,'office':20,'commercial':12,'industrial':10,
                 'retail':8,'school':10,'hospital':15,'church':20,'yes':8}}
    return defaults.get(row.get('building_type','yes'), 8.0)

bdf['height_m'] = bdf.apply(_height, axis=1).clip(2.0, 300.0)

# ── Optional noise join ───────────────────────────────────────────────────────
noise_path = {noise_arg!r}
if noise_path and os.path.exists(noise_path):
    print(f"[buildings] Joining noise data from {{noise_path}} ...")
    noise = gpd.read_file(noise_path)
    # Use building centroid for the spatial join (faster than polygon-polygon)
    cents = bdf.copy()
    cents['geometry'] = cents.geometry.centroid
    cents = cents.to_crs(noise.crs)
    joined = gpd.sjoin(cents[['osm_id','geometry']],
                       noise[['noise_d','distance_band','geometry']],
                       how='left', predicate='within')
    # Aggregate: take mean noise_d per building (centroid may hit multiple bands)
    agg = joined.groupby('osm_id')['noise_d'].mean().reset_index()
    agg.columns = ['osm_id', 'noise_leq']
    bdf = bdf.merge(agg, on='osm_id', how='left')
    bdf['noise_leq'] = bdf['noise_leq'].fillna(0.0)
    print(f"[buildings] Noise joined: {{(bdf['noise_leq']>0).sum()}} buildings with noise data")
else:
    bdf['noise_leq'] = 0.0

# ── Collect output columns ────────────────────────────────────────────────────
out_cols = ['osm_id', 'building_type', 'height_m', 'noise_leq', 'geometry']
for c in ['name', 'amenity', 'addr:street']:
    if c in bdf.columns:
        out_cols.insert(3, c)

bdf = gpd.GeoDataFrame(bdf[out_cols], crs=bdf.crs)
bdf.to_file({out_geojson!r}, driver='GeoJSON')
print(f"[buildings] Saved {{len(bdf)}} buildings -> {out_geojson!r}")
print("BUILDINGS_COMPLETE")
"""

    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="rapidx_bldg_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(script)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["GDAL_DATA"] = f"{_RAPIDXPY_CONDA_ENV}/Library/share/gdal"
        env["PROJ_LIB"] = f"{_RAPIDXPY_CONDA_ENV}/Library/share/proj"

        result = _sp.run(
            [python_exe, script_path],
            input=b"",
            capture_output=True, timeout=300, env=env
        )
        _out = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        _err = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        for i in range(0, max(len(_out), 1), 2000):
            chunk = _out[i:i + 2000]
            if chunk.strip():
                carb.log_info(f"[building_ops] {chunk}")
        for i in range(0, max(len(_err), 1), 2000):
            chunk = _err[i:i + 2000]
            if chunk.strip():
                carb.log_warn(f"[building_ops] {chunk}")
        carb.log_info(f"[building_ops] returncode: {result.returncode}")

        if result.returncode != 0 or "BUILDINGS_COMPLETE" not in _out:
            err = (_err or _out)[-800:]
            return False, f"Building download failed:\n{err}", None

        return True, f"Buildings saved -> {out_geojson}", out_geojson

    except _sp.TimeoutExpired:
        return False, "Building download timed out (5 min)", None
    except Exception as exc:
        carb.log_error(f"[building_ops] {exc}")
        return False, f"Building error: {exc}", None
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass