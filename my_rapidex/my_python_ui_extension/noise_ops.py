"""
noise_ops.py
============
Traffic noise decay computation using the CNOSSOS-EU method.

Reads  Elite0LinkResults.csv  (rapidxpy estimation output) +
       edges.geojson           (network geometry),
computes per-link Leq noise levels (notebook formula), applies
CNOSSOS-EU geometrical-spreading decay in 5 distance bands
[0-10 | 10-25 | 25-50 | 50-100 | 100-200 m], and writes:

  <output_dir>/link_noise.geojson          — per-link noise
  <output_dir>/noise_decay_bands.geojson   — full annular rings
  <output_dir>/noise_decay_dissolved.geojson — dissolved by band + dB bin

All heavy computation runs in a subprocess so Isaac Sim is never blocked.
"""

import os
import carb

# ── Paths ─────────────────────────────────────────────────────────────────────
_EXT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
).replace("\\", "/")

_RAPIDXPY_CONDA_ENV = "C:/users/sakshi/miniforge3/envs/rapidxpy"
_PYTHON_CANDIDATES = [
    f"{_RAPIDXPY_CONDA_ENV}/python.exe",
    "C:/isaac-sim/kit/python/python.exe",
    "C:/isaac-sim/python/python.exe",
]

# ── Projected CRS per city for accurate metre-based buffering ─────────────────
_UTM_FALLBACK = "EPSG:32633"   # UTM zone 33N — central Europe default
_CITY_UTM = {
    "munich": "EPSG:32632",    "münchen": "EPSG:32632",
    "berlin": "EPSG:32633",    "hamburg": "EPSG:32632",
    "frankfurt": "EPSG:32632", "cologne": "EPSG:32632",  "köln": "EPSG:32632",
    "stuttgart": "EPSG:32632", "dresden": "EPSG:32633",  "leipzig": "EPSG:32633",
    "hannover": "EPSG:32632",  "nuremberg": "EPSG:32632","nürnberg": "EPSG:32632",
    "vienna": "EPSG:32633",    "prague": "EPSG:32633",   "warsaw": "EPSG:32634",
    "paris": "EPSG:32631",     "london": "EPSG:32630",
    "amsterdam": "EPSG:32631", "brussels": "EPSG:32631",
    "zurich": "EPSG:32632",    "geneva": "EPSG:32632",
}

# Distance bands (metres) — CNOSSOS-EU geometrical spreading
DISTANCE_BANDS = [0, 10, 25, 50, 100, 200]
REFERENCE_DIST = 7.5   # metres — reference distance in decay formula (matches viz)


def _find_python():
    import shutil
    for c in _PYTHON_CANDIDATES:
        if os.path.isfile(c):
            return c.replace("\\", "/")
    return shutil.which("python") or "python"


def find_elite_csv(search_root):
    """
    Recursively search *search_root* for Elite0LinkResults.csv.
    Returns the last (most recent by sort order) match, or None.
    """
    import glob as _glob
    matches = _glob.glob(
        os.path.join(search_root, "**", "Elite0LinkResults.csv"), recursive=True)
    return sorted(matches)[-1].replace("\\", "/") if matches else None


def find_edges_geojson(project_dir):
    """Find edges.geojson under *project_dir*, handling the double-dir layout."""
    import glob as _glob
    for pat in [
        os.path.join(project_dir, "Network", "edges.geojson"),
        os.path.join(project_dir, "edges.geojson"),
        os.path.join(project_dir, "*", "Network", "edges.geojson"),
        os.path.join(project_dir, "*", "*", "Network", "edges.geojson"),
    ]:
        hits = _glob.glob(pat)
        if hits:
            return hits[0].replace("\\", "/")
    return None


# ─────────────────────────────────────────────────────────────────────────────

def compute_noise_decay(city_name, project_dir, elite_csv=None,
                        edges_geojson=None, output_dir=None):
    """
    Run noise decay computation in an isolated subprocess.

    Parameters
    ----------
    city_name     : str       Used for UTM CRS selection.
    project_dir   : str       Project root (contains Network/ subfolder).
    elite_csv     : str|None  Full path to Elite0LinkResults.csv.
                              If None, searched recursively under project_dir.
    edges_geojson : str|None  Full path to edges.geojson override.
                              If None, searched under project_dir.
    output_dir    : str|None  Where to write GeoJSON outputs.
                              Defaults to <project_dir>/Noise/.

    Returns
    -------
    (success: bool, message: str, geojson_path: str|None)
        geojson_path points to noise_decay_dissolved.geojson on success.
    """
    import subprocess as _sp
    import tempfile

    # ── Resolve inputs ────────────────────────────────────────────────────────
    if elite_csv and os.path.exists(elite_csv):
        elite_csv_path = elite_csv.replace("\\", "/")
    else:
        elite_csv_path = find_elite_csv(project_dir)
        if not elite_csv_path:
            return False, f"Elite0LinkResults.csv not found under {project_dir}", None

    if edges_geojson and os.path.exists(edges_geojson):
        edges_path = edges_geojson.replace("\\", "/")
    else:
        edges_path = find_edges_geojson(project_dir)
    if not edges_path:
        return False, f"edges.geojson not found under {project_dir}", None

    if output_dir is None:
        output_dir = os.path.join(project_dir, "Noise")
    output_dir = output_dir.replace("\\", "/")

    utm_crs = _CITY_UTM.get(city_name.lower().split(",")[0].strip(), _UTM_FALLBACK)
    python_exe = _find_python()

    carb.log_info(f"[noise_ops] Elite CSV  : {elite_csv_path}")
    carb.log_info(f"[noise_ops] edges.geojson: {edges_path}")
    carb.log_info(f"[noise_ops] Output dir : {output_dir}")
    carb.log_info(f"[noise_ops] UTM CRS    : {utm_crs}")

    dissolved_path = os.path.join(output_dir, "noise_decay_dissolved.geojson").replace("\\", "/")
    bands_path     = os.path.join(output_dir, "noise_decay_bands.geojson").replace("\\", "/")

    # ── Subprocess script ─────────────────────────────────────────────────────
    script = f"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, {_EXT_ROOT!r})

import pandas as pd
import geopandas as gpd
import numpy as np

os.makedirs({output_dir!r}, exist_ok=True)

# ── 1. Load Elite0LinkResults.csv ─────────────────────────────────────────────
print("[noise] Loading Elite0LinkResults.csv ...")
link_df = pd.read_csv({elite_csv_path!r})
link_df.columns = [c.strip().lower().replace(' ', '_') for c in link_df.columns]
print(f"[noise] CSV columns : {{list(link_df.columns)}}")
print(f"[noise] CSV rows    : {{len(link_df)}}")

def _find(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None

_c = link_df.columns.tolist()
from_col   = _find(_c, ['from_node','from','fromnodeid','init_node','node_from','a','tail'])
to_col     = _find(_c, ['to_node',  'to',  'tonodeid',  'term_node','node_to',  'b','head'])
flow_col   = _find(_c, ['flow','volume','assigned_volume','assigned_flow',
                         'veh','count','pcu','link_flow','q'])
length_col = _find(_c, ['length','len','distance','link_length'])
cost_col   = _find(_c, ['cost','travel_time','time','tt','traveltime','congested_time'])
fft_col    = _find(_c, ['fft','free_flow_time','freeflowtime','free_flow_tt','t0'])

if from_col is None or to_col is None:
    raise ValueError(f"Cannot find from/to columns in Elite CSV. Got: {{_c}}")
if flow_col is None:
    raise ValueError(f"Cannot find flow/volume column in Elite CSV. Got: {{_c}}")

rn = {{}}
if from_col   != 'from':   rn[from_col]   = 'from'
if to_col     != 'to':     rn[to_col]     = 'to'
if flow_col   != 'flow':   rn[flow_col]   = 'flow'
if length_col and length_col != 'length': rn[length_col] = 'length'
if cost_col   and cost_col   != 'cost':   rn[cost_col]   = 'cost'
link_df = link_df.rename(columns=rn)

# ── 2. Load edges.geojson ─────────────────────────────────────────────────────
print("[noise] Loading edges.geojson ...")
edges = gpd.read_file({edges_path!r})
edges.columns = [c.strip().lower() for c in edges.columns]

e_from = _find(edges.columns.tolist(), ['from','from_node','fromnodeid','init_node','u'])
e_to   = _find(edges.columns.tolist(), ['to',  'to_node',  'tonodeid',  'term_node','v'])
if e_from and e_from != 'from': edges = edges.rename(columns={{e_from: 'from'}})
if e_to   and e_to   != 'to':   edges = edges.rename(columns={{e_to:   'to'}})
print(f"[noise] Edges columns: {{list(edges.columns)}}")

# ── 3. Merge ──────────────────────────────────────────────────────────────────
keep_cols = ['from','to','flow'] + [c for c in ['length','cost'] if c in link_df.columns]
merged = edges.merge(link_df[keep_cols], on=['from','to'], how='left')

# Compute length from geometry if missing in CSV
if 'length' not in merged.columns or merged['length'].isna().all():
    _proj = merged.to_crs({utm_crs!r})
    merged['length'] = _proj.geometry.length
    del _proj
if 'cost' not in merged.columns or merged['cost'].isna().all():
    merged['cost'] = merged['length'] / 13.89   # fallback: ~50 km/h

merged['flow']   = merged['flow'].fillna(0)
merged['length'] = merged['length'].fillna(100)
merged['cost']   = merged['cost'].fillna(merged['length'] / 13.89)

print(f"[noise] Merged rows: {{len(merged)}}  links with flow>0: {{(merged['flow']>0).sum()}}")

# ── 4. Noise formula (notebook / CNOSSOS-inspired) ───────────────────────────
L0 = 37.3

def calculate_noise(row):
    flow   = float(row['flow'])
    length = float(row['length'])
    cost   = float(row['cost'])
    if flow <= 0:
        return 0.0
    speed_kmh = min(max((length / max(cost * 60, 1e-6)) * 3.6, 5.0), 130.0)
    Lcar  = 27.7 + 10 * np.log10(1 + (0.02 * speed_kmh) ** 3)
    Lhg   = 23.1 + 12.5 * np.log10(speed_kmh)
    f2    = 10 ** (0.1 * (Lhg - Lcar))
    k2    = 2
    speedC = (Lcar - L0
              + 10 * np.log10((100 + (f2 - 1)) * k2)
              / (100 + 8.23 * k2))
    Noiseavg = L0 + 10 * np.log10(flow * (1 + 0.082 * 3))
    return float(Noiseavg + speedC)

print("[noise] Computing per-link Leq ...")
merged['noise'] = merged.apply(calculate_noise, axis=1)
active = merged[merged['noise'] > 0]['noise']
print(f"[noise] Leq stats — min={{active.min():.1f}}  max={{active.max():.1f}}  mean={{active.mean():.1f}} dB")

link_out = os.path.join({output_dir!r}, "link_noise.geojson")
merged[['from','to','noise','geometry']].to_file(link_out, driver='GeoJSON')
print(f"[noise] Link noise saved -> {{link_out}}")

# ── 5. CNOSSOS-EU distance decay rings ───────────────────────────────────────
# Buffer in UTM (metres), export in WGS-84
distances = [0, 10, 25, 50, 100, 200]
active_edges = merged[merged['noise'] > 0].copy()
active_proj  = active_edges.to_crs({utm_crs!r})

ring_layers = []
print("[noise] Computing distance bands ...")
for d_outer, d_inner in zip(distances[1:], distances[:-1]):
    temp = active_proj.copy()
    ring_geom = temp.geometry.buffer(d_outer).difference(temp.geometry.buffer(d_inner))
    ring_geom = ring_geom.buffer(0)           # fix topology artefacts
    temp = temp[~ring_geom.is_empty].copy()
    temp['geometry'] = ring_geom[~ring_geom.is_empty]

    d_mid = (d_outer + d_inner) / 2.0
    temp['noise_d'] = (temp['noise']
                       - 10 * np.log10(max(d_mid, 1.0) / {REFERENCE_DIST})).clip(lower=0)
    temp['distance_band'] = f"{{d_inner}}-{{d_outer}}m"
    ring_layers.append(temp)
    print(f"[noise]   {{d_inner:3d}}-{{d_outer:3d}} m : "
          f"{{len(temp):5d}} rings  mean_noise_d={{temp['noise_d'].mean():.1f}} dB")

rings = gpd.GeoDataFrame(pd.concat(ring_layers, ignore_index=True), crs={utm_crs!r})
rings = rings.to_crs("EPSG:4326")
rings['noise_d'] = rings['noise_d'].clip(lower=0)

export_cols = ['from','to','distance_band','noise_d','geometry']
rings[export_cols].to_file({bands_path!r}, driver='GeoJSON')
print(f"[noise] Full bands  -> {{len(rings)}} features -> {bands_path!r}")

# ── 6. Dissolved version (one polygon per band×dBbin — small & fast to load) ─
bins   = [0, 50, 55, 60, 65, 70, 200]
labels = ['<50dB','50-55dB','55-60dB','60-65dB','65-70dB','>70dB']
rings['noise_bin'] = pd.cut(rings['noise_d'], bins=bins,
                             labels=labels, include_lowest=True).astype(str)
dissolved = (rings[export_cols + ['noise_bin']]
             .dissolve(by=['distance_band','noise_bin'], aggfunc='mean')
             .reset_index())
dissolved.to_file({dissolved_path!r}, driver='GeoJSON')
print(f"[noise] Dissolved   -> {{len(dissolved)}} features -> {dissolved_path!r}")

print("NOISE_COMPLETE")
"""

    # ── Run subprocess ────────────────────────────────────────────────────────
    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="rapidx_noise_")
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
        _stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        _stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        for i in range(0, max(len(_stdout), 1), 2000):
            chunk = _stdout[i:i + 2000]
            if chunk.strip():
                carb.log_info(f"[noise_ops] {chunk}")
        for i in range(0, max(len(_stderr), 1), 2000):
            chunk = _stderr[i:i + 2000]
            if chunk.strip():
                carb.log_warn(f"[noise_ops] {chunk}")
        carb.log_info(f"[noise_ops] returncode: {result.returncode}")

        if result.returncode != 0 or "NOISE_COMPLETE" not in _stdout:
            err = (_stderr or _stdout)[-800:]
            return False, f"Noise computation failed:\n{err}", None

        out = dissolved_path if os.path.exists(dissolved_path) else bands_path
        return True, f"Noise decay computed - {len(os.listdir(output_dir))} files in {output_dir}", out

    except _sp.TimeoutExpired:
        return False, "Noise computation timed out (5 min)", None
    except Exception as exc:
        carb.log_error(f"[noise_ops] {exc}")
        return False, f"Noise error: {exc}", None
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass