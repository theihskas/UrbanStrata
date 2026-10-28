"""
network_ops.py
==============
Helpers for RapidXPy network operations: geocoding, download, zoning, fix.
All functions are pure (no Omniverse extension class state) and return a
(success: bool, message: str) tuple so extension.py stays thin.

NOTE: apply_zoning is implemented locally (H3 / square grid) and does NOT
call rapidx_network.zoning() — that function triggers internal Kit stage
operations that crash Isaac Sim.
"""

import os
import json
from argparse import Namespace

import carb

WORLDPOP_DIR   = "D:/OpenDataSets/WorldPop"
SAFEGRAPH_DIR  = "D:/SafeGraph"

# Root of this extension — contains the bundled rapidxpy/ package
_EXT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
).replace("\\", "/")

# Conda env that has rapidxpy installed
_RAPIDXPY_CONDA_ENV = "C:/users/sakshi/miniforge3/envs/rapidxpy"

# Known Python locations — checked in order, first match wins
_PYTHON_CANDIDATES = [
    # Conda env with rapidxpy (primary)
    f"{_RAPIDXPY_CONDA_ENV}/python.exe",
    # Isaac Sim bundled Python
    "C:/isaac-sim/kit/python/python.exe",
    "C:/isaac-sim/python/python.exe",
]


def _find_rapidxpy_python():
    """
    Return a Python interpreter that has the scientific dependencies
    (geopandas, h3, osmnx, …) needed to run the bundled rapidxpy scripts.

    We use the conda env Python for its dependencies, but the subprocess
    script itself inserts _EXT_ROOT at the front of sys.path so the
    bundled extension rapidxpy/ is what actually gets imported — not the
    conda-installed copy.
    """
    import shutil

    for candidate in _PYTHON_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate.replace("\\", "/")

    found = shutil.which("python") or shutil.which("python3")
    return found or "python"


# ── Country name → ISO3 map for WorldPop lookup ───────────────────────────────
_COUNTRY_ISO3 = {
    # Country names
    "germany": "deu", "france": "fra", "italy": "ita", "spain": "esp",
    "united kingdom": "gbr", "uk": "gbr", "england": "gbr",
    "netherlands": "nld", "belgium": "bel", "switzerland": "che",
    "austria": "aut", "poland": "pol", "czechia": "cze",
    "czech republic": "cze", "slovakia": "svk", "hungary": "hun",
    "romania": "rou", "bulgaria": "bgr", "greece": "grc",
    "portugal": "prt", "sweden": "swe", "norway": "nor", "denmark": "dnk",
    "finland": "fin", "estonia": "est", "latvia": "lva", "lithuania": "ltu",
    "croatia": "hrv", "serbia": "srb", "ukraine": "ukr", "russia": "rus",
    "turkey": "tur", "usa": "usa", "united states": "usa", "united states of america": "usa",
    "canada": "can", "mexico": "mex", "brazil": "bra", "argentina": "arg",
    "china": "chn", "india": "ind", "japan": "jpn", "south korea": "kor",
    "australia": "aus", "philippines": "phl", "indonesia": "idn",
    "vietnam": "vnm", "thailand": "tha", "malaysia": "mys",
    "nigeria": "nga", "south africa": "zaf", "egypt": "egy",
    "ethiopia": "eth", "kenya": "ken", "ghana": "gha",
    "colombia": "col", "peru": "per", "chile": "chl",
    # German cities → deu
    "munich": "deu", "münchen": "deu", "berlin": "deu", "hamburg": "deu",
    "frankfurt": "deu", "cologne": "deu", "köln": "deu", "düsseldorf": "deu",
    "stuttgart": "deu", "dresden": "deu", "leipzig": "deu", "dortmund": "deu",
    "essen": "deu", "bremen": "deu", "hannover": "deu", "nuremberg": "deu",
    "nürnberg": "deu", "duisburg": "deu", "bochum": "deu", "wuppertal": "deu",
    # Other major cities
    "paris": "fra", "lyon": "fra", "marseille": "fra",
    "rome": "ita", "milan": "ita", "naples": "ita",
    "madrid": "esp", "barcelona": "esp",
    "london": "gbr", "manchester": "gbr", "birmingham": "gbr",
    "amsterdam": "nld", "rotterdam": "nld",
    "brussels": "bel", "antwerp": "bel",
    "vienna": "aut", "graz": "aut",
    "zurich": "che", "geneva": "che",
    "warsaw": "pol", "krakow": "pol",
    "prague": "cze", "budapest": "hun",
    "manila": "phl", "cebu": "phl",
    "mumbai": "ind", "delhi": "ind", "bangalore": "ind", "kolkata": "ind",
    "beijing": "chn", "shanghai": "chn", "guangzhou": "chn",
    "tokyo": "jpn", "osaka": "jpn",
    "lagos": "nga", "cairo": "egy", "nairobi": "ken",
    "sydney": "aus", "melbourne": "aus",
    "toronto": "can", "vancouver": "can", "montreal": "can",
    "new york": "usa", "los angeles": "usa", "chicago": "usa", "houston": "usa",
    "mexico city": "mex", "sao paulo": "bra", "buenos aires": "arg",
    "istanbul": "tur", "ankara": "tur",
    "moscow": "rus", "saint petersburg": "rus",
}


def get_worldpop_tif(city_name, worldpop_dir=WORLDPOP_DIR):
    """
    Return the path to the WorldPop raster for the country of *city_name*.
    Files are named  <iso3>_ppp_2020_UNadj_constrained.tif
    Returns None when nothing is found (zoning still works, just unweighted).
    """
    import glob as _glob

    if not os.path.isdir(worldpop_dir):
        carb.log_warn(f"[network_ops] WorldPop dir not found: {worldpop_dir}")
        return None

    # Build a list of tokens to try: all comma-separated parts, country last → first
    tokens = [t.strip().lower() for t in city_name.replace(";", ",").split(",")]
    tokens_to_try = list(dict.fromkeys(reversed(tokens)))  # country-first, deduped

    iso3 = None
    for token in tokens_to_try:
        # 1. Direct map lookup (handles both city names and country names)
        if token in _COUNTRY_ISO3:
            iso3 = _COUNTRY_ISO3[token]
            break
        # 2. pycountry fuzzy match
        try:
            import pycountry
            results = pycountry.countries.search_fuzzy(token)
            if results:
                iso3 = results[0].alpha_3.lower()
                break
        except Exception:
            pass

    if iso3:
        candidate = os.path.join(worldpop_dir,
                                  f"{iso3}_ppp_2020_UNadj_constrained.tif")
        if os.path.exists(candidate):
            carb.log_info(f"[network_ops] WorldPop tif: {candidate}")
            return candidate

    # Fallback: glob with iso3 prefix or first 3 chars of first token
    prefix = iso3 or tokens_to_try[0][:3]
    matches = _glob.glob(os.path.join(worldpop_dir, f"{prefix}*_ppp_2020*.tif"))
    if matches:
        carb.log_info(f"[network_ops] WorldPop tif (glob): {matches[0]}")
        return matches[0]

    # Last resort: try the city name directly as first 3 chars (e.g. "Munich" → "mun*")
    # and also geocode the country from lat/lon
    try:
        import reverse_geocode
        lat, lon = get_city_coordinates(city_name)
        result   = reverse_geocode.get((lat, lon))
        cc2      = result.get("country_code", "").lower()   # 2-letter code
        if cc2:
            matches = _glob.glob(os.path.join(worldpop_dir, f"{cc2}?_ppp_2020*.tif"))
            if not matches:
                # Try pycountry alpha2 → alpha3
                try:
                    import pycountry
                    c = pycountry.countries.get(alpha_2=cc2.upper())
                    if c:
                        candidate = os.path.join(
                            worldpop_dir,
                            f"{c.alpha_3.lower()}_ppp_2020_UNadj_constrained.tif")
                        if os.path.exists(candidate):
                            carb.log_info(f"[network_ops] WorldPop tif (reverse-geocode): {candidate}")
                            return candidate
                except Exception:
                    pass
            if matches:
                carb.log_info(f"[network_ops] WorldPop tif (reverse-geocode glob): {matches[0]}")
                return matches[0]
    except Exception:
        pass

    carb.log_warn(
        f"[network_ops] No WorldPop tif found for '{city_name}' in {worldpop_dir}")
    return None


# ── City terrain elevation (metres above WGS-84 ellipsoid / sea level) ────────
# Used to offset the Cesium georeference so z=0 in stage space = ground level.
# Values are approximate city-centre terrain elevations.
_CITY_ELEVATION = {
    # Germany
    "munich": 520,   "münchen": 520,
    "berlin": 34,    "hamburg": 6,
    "frankfurt": 112, "cologne": 57,  "köln": 57,
    "stuttgart": 245, "dresden": 113, "leipzig": 115,
    "hannover": 55,   "nuremberg": 309, "nürnberg": 309,
    "dortmund": 86,   "essen": 116,   "düsseldorf": 38,
    "bremen": 9,      "bochum": 100,
    # Western Europe
    "paris": 35,     "lyon": 170,   "marseille": 24,
    "london": 11,    "manchester": 38, "birmingham": 140,
    "amsterdam": 0,  "rotterdam": 0,
    "brussels": 56,  "antwerp": 9,
    "vienna": 171,   "graz": 353,
    "zurich": 408,   "geneva": 375,
    "rome": 21,      "milan": 122,   "naples": 17,
    "madrid": 667,   "barcelona": 12,
    # Eastern Europe
    "warsaw": 100,   "krakow": 219,
    "prague": 202,   "budapest": 108,
    # Americas
    "new york": 10,  "los angeles": 71,  "chicago": 176,
    "houston": 14,   "toronto": 76,      "montreal": 30,
    "vancouver": 0,  "mexico city": 2240, "sao paulo": 760,
    "buenos aires": 25,
    # Asia / Pacific
    "tokyo": 6,      "osaka": 8,
    "beijing": 43,   "shanghai": 4,   "guangzhou": 10,
    "mumbai": 14,    "delhi": 216,    "bangalore": 920,
    "sydney": 25,    "melbourne": 31,
    "singapore": 15, "kuala lumpur": 22,
    # Africa / Middle East
    "cairo": 23,     "lagos": 41,    "nairobi": 1795,
    "istanbul": 39,  "ankara": 938,
    # Russia
    "moscow": 156,   "saint petersburg": 5,
}


def get_city_elevation(city_name):
    """
    Return approximate terrain elevation (m) for the city, or 0 if unknown.
    Setting this on the Cesium georeference makes z=0 in stage space = ground.
    """
    # Try full name first, then just the first comma-separated part
    for token in (city_name.lower(), city_name.lower().split(",")[0].strip()):
        if token in _CITY_ELEVATION:
            return _CITY_ELEVATION[token]
    return 0


# ── SafeGraph POI lookup ──────────────────────────────────────────────────────

def get_safegraph_poi(city_name, safegraph_dir=SAFEGRAPH_DIR):
    """
    Return the path to the SafeGraph POI parquet file for the country of
    *city_name*, or None when the directory or file is not found.

    Accepts any of these filename patterns (case-insensitive on the prefix):
      {iso3}_POI.parquet          (rapidxpy default)
      {iso3}_POI_2022.parquet     (year variant)
      {iso3}2022.parquet          (compact variant, e.g. deu2022.parquet)
      {iso3}*.parquet             (any parquet starting with the ISO3 code)

    Files that are 0 bytes are skipped — they are artefacts from failed runs.
    The largest matching file wins (most likely to be complete).
    """
    import glob as _glob

    if not os.path.isdir(safegraph_dir):
        carb.log_warn(f"[network_ops] SafeGraph dir not found: {safegraph_dir}")
        return None

    # Resolve ISO3 from city name using the same dict used for WorldPop
    tokens = [t.strip().lower() for t in city_name.replace(";", ",").split(",")]
    iso3 = None
    for token in list(dict.fromkeys(reversed(tokens))):
        if token in _COUNTRY_ISO3:
            iso3 = _COUNTRY_ISO3[token]
            break
        try:
            import pycountry
            results = pycountry.countries.search_fuzzy(token)
            if results:
                iso3 = results[0].alpha_3.lower()
                break
        except Exception:
            pass

    if iso3 is None:
        carb.log_warn(f"[network_ops] Could not resolve ISO3 for SafeGraph lookup: {city_name}")
        return None

    # Glob patterns — ordered from most specific to least specific.
    # Both lower and upper case prefixes are tried.
    patterns = [
        os.path.join(safegraph_dir, f"{iso3}_POI.parquet"),
        os.path.join(safegraph_dir, f"{iso3}_POI_*.parquet"),
        os.path.join(safegraph_dir, f"{iso3.upper()}_POI*.parquet"),
        os.path.join(safegraph_dir, f"{iso3}*.parquet"),       # e.g. deu2022.parquet
        os.path.join(safegraph_dir, f"{iso3.upper()}*.parquet"),
    ]

    candidates = []
    for pat in patterns:
        for path in _glob.glob(pat):
            size = os.path.getsize(path)
            if size > 0:
                candidates.append((size, path))

    if not candidates:
        carb.log_warn(
            f"[network_ops] No non-empty SafeGraph parquet found for "
            f"'{city_name}' ({iso3}) in {safegraph_dir}")
        return None

    # Pick the largest file (most complete)
    best = max(candidates, key=lambda t: t[0])[1].replace("\\", "/")
    carb.log_info(f"[network_ops] SafeGraph POI: {best}")
    return best


# ── Geocoding ─────────────────────────────────────────────────────────────────

_CITY_FALLBACK = {
    "Dresden, Germany":   (51.0504, 13.7373),
    "Berlin, Germany":    (52.5200, 13.4050),
    "Munich, Germany":    (48.1351, 11.5820),
    "Hamburg, Germany":   (53.5511,  9.9937),
    "Leipzig, Germany":   (51.3397, 12.3731),
    "Frankfurt, Germany": (50.1109,  8.6821),
}


def get_city_coordinates(city_name):
    """
    Return (lat, lon) for *city_name*.
    Tries osmnx → geopy → static fallback → Dresden default.
    """
    try:
        import osmnx as ox
        lat, lon = ox.geocode(city_name)
        return (lat, lon)
    except Exception:
        pass

    try:
        from geopy.geocoders import Nominatim
        geo = Nominatim(user_agent="my_rapidex_extension")
        loc = geo.geocode(city_name, timeout=10)
        if loc:
            return (loc.latitude, loc.longitude)
    except Exception:
        pass

    # Static fallback
    safe = city_name.replace(" ", "_").replace(",", "").replace("(", "").replace(")", "")
    for k, v in _CITY_FALLBACK.items():
        if k == city_name or k.replace(" ", "_").replace(",", "") == safe:
            return v

    carb.log_warn(f"[network_ops] Could not geocode '{city_name}', defaulting to Dresden")
    return (51.0504, 13.7373)


# ── Network download ──────────────────────────────────────────────────────────

def download_network(city_name, project_dir, road_types, speeds, rapidx_network):
    """
    Download the street network for *city_name* into *project_dir*/Network/.
    Returns (success, message).
    """
    os.makedirs(project_dir, exist_ok=True)
    worldpop_tif = get_worldpop_tif(city_name)
    carb.log_info(f"[network_ops] WorldPop tif for download: {worldpop_tif}")

    original_cwd = os.getcwd()
    os.chdir(project_dir)
    try:
        args = Namespace(
            place=city_name,
            north_lat=None, south_lat=None, east_lon=None, west_lon=None,
            vectorfile=None,
            default_speed=speeds,
            road_types=road_types,
            include=road_types,
            target=None,
            deprivationtif=worldpop_tif,
            y=True,
        )
        rapidx_network.download(args)
        return True, f"Network downloaded for {city_name}"
    except Exception as exc:
        carb.log_error(f"[network_ops] download_network: {exc}")
        return False, f"Download failed: {exc}"
    finally:
        os.chdir(original_cwd)


# ── Zoning ────────────────────────────────────────────────────────────────────

def apply_zoning(city_name, project_dir, zone_type, zone_res, zone_levels,
                  zone_file, _rapidx_network=None):
    """
    Run rapidxpy's zoning logic in a **completely isolated subprocess** so it
    cannot touch Isaac Sim's USD stage (which would trigger a save dialog and
    crash the app).

    After the subprocess exits successfully the caller should look for the
    generated Zones.geojson in Zones_H3/ or Zones_default/ and convert it to
    USD via geojson_usd_writer.

    Returns (success: bool, message: str, zones_folder: str|None).
    The third element lets the caller know where to find the zone files.
    """
    import tempfile
    import subprocess as _sp

    # ── Resolve the actual Network directory ──────────────────────────────────
    network_dir = os.path.join(project_dir, "Network")
    if not os.path.exists(os.path.join(network_dir, "edges.geojson")):
        # Handle rapidxpy double-directory (e.g. Munich/Munich/Network)
        try:
            for entry in os.scandir(project_dir):
                if entry.is_dir():
                    alt = os.path.join(entry.path, "Network")
                    if os.path.exists(os.path.join(alt, "edges.geojson")):
                        network_dir = alt
                        project_dir = entry.path
                        break
        except Exception:
            pass

    if not os.path.exists(os.path.join(network_dir, "edges.geojson")):
        return False, "edges.geojson not found — download network first", None

    # ── Resolve WorldPop raster and SafeGraph POI for this city/country ─────
    worldpop_tif    = get_worldpop_tif(city_name)
    safegraph_poi   = get_safegraph_poi(city_name)
    carb.log_info(f"[network_ops] WorldPop tif: {worldpop_tif}")
    carb.log_info(f"[network_ops] SafeGraph POI: {safegraph_poi}")

    # Choose API based on what data is available
    _api      = "safegraph" if safegraph_poi else "osm"
    _poi_arg  = safegraph_poi or None   # None → OSM downloads its own POIs

    # ── Find the Python that has rapidxpy installed (conda env) ─────────────
    python_exe = _find_rapidxpy_python()
    carb.log_info(f"[network_ops] Using Python for zoning subprocess: {python_exe}")

    # Prepend the extension root so the bundled rapidxpy/ takes priority
    # worldpop_tif must be a Path object — rapidxpy calls .exists() on it directly
    _pop_tif_repr = f"Path({worldpop_tif!r})" if worldpop_tif else "None"
    _poi_repr     = f"Path({_poi_arg!r})"      if _poi_arg   else "None"
    _env_file = os.path.join(_EXT_ROOT, "rapidxpy", ".env").replace("\\", "/")
    script = f"""
import sys, os
from pathlib import Path
sys.path.insert(0, {_EXT_ROOT!r})

# Load API keys from bundled .env
_env_path = {_env_file!r}
if os.path.exists(_env_path):
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                if _v.strip():
                    os.environ[_k.strip()] = _v.strip()

from rapidxpy.scripts import network as rapidx_network
from argparse import Namespace
os.chdir({project_dir!r})
_pop_tif     = {_pop_tif_repr}
_country_poi = {_poi_repr}
args = Namespace(
    zone_type={zone_type!r},
    zone_resolution={zone_res!r},
    zone_levels={zone_levels!r},
    populationtif=_pop_tif,
    deprivationtif=_pop_tif,
    zonefile={zone_file!r},
    api={_api!r},
    apikey=None,
    countrypois=_country_poi,
    place_boundary_shp=None,
    min_cent=3,
    max_cent=4,
    min_grid_size=500,
    max_grid_size=2000,
    dist_btw_cent=200,
    epsilon=0.0,
    max_cap_tier=3,
    mid_cap_tier=2,
    min_cap_tier=1,
    target={network_dir!r},
    y=True,
)
rapidx_network.zoning(args)
print("ZONING_COMPLETE")
"""

    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="rapidx_zone_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(script)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["GDAL_DATA"] = f"{_RAPIDXPY_CONDA_ENV}/Library/share/gdal"
        env["PROJ_LIB"] = f"{_RAPIDXPY_CONDA_ENV}/Library/share/proj"

        result = _sp.run(
            [python_exe, script_path],
            # Pre-answer any interactive "Overwrite? (y/n):" prompts that rapidxpy
            # may emit via input().  Without this, stdin is EOF and zoning crashes
            # with "EOFError: EOF when reading a line".
            input="y\ny\ny\n",
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace", env=env
        )
        # Log full stdout/stderr in 2000-char chunks so nothing is hidden
        _stdout = result.stdout or ""
        _stderr = result.stderr or ""
        for _chunk_start in range(0, max(len(_stdout), 1), 2000):
            chunk = _stdout[_chunk_start:_chunk_start + 2000]
            if chunk.strip():
                carb.log_info(f"[network_ops] zoning stdout: {chunk}")
        for _chunk_start in range(0, max(len(_stderr), 1), 2000):
            chunk = _stderr[_chunk_start:_chunk_start + 2000]
            if chunk.strip():
                carb.log_warn(f"[network_ops] zoning stderr: {chunk}")
        carb.log_info(f"[network_ops] zoning returncode: {result.returncode}")

        if result.returncode != 0 or "ZONING_COMPLETE" not in result.stdout:
            err = (_stderr or _stdout)[-800:]   # last 800 chars = the actual error
            return False, f"Zoning subprocess failed: {err}", None

    except _sp.TimeoutExpired:
        return False, "Zoning timed out after 10 minutes", None
    except Exception as exc:
        carb.log_error(f"[network_ops] apply_zoning subprocess: {exc}")
        return False, f"Zoning error: {exc}", None
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass

    # ── Locate the generated zones folder ────────────────────────────────────
    for sub in ("Zones_H3", "Zones_default", "Zones_custom"):
        candidate = os.path.join(project_dir, sub)
        if os.path.exists(os.path.join(candidate, "Zones.geojson")):
            carb.log_info(f"[network_ops] Zones found: {candidate}")
            return True, f"{zone_type} zoning complete for {city_name}", candidate

    return True, f"{zone_type} zoning complete — zone folder not detected (check {project_dir})", None


# ── Network speed fix ─────────────────────────────────────────────────────────

def fix_network_speeds(city_name, project_dir, rapidx_network):
    """
    Update link speeds from the most recent LinkTT CSV in Traveltimes/.
    Returns (success, message).
    """
    network_dir = os.path.join(project_dir, "Network")
    tt_dir = os.path.join(project_dir, "Traveltimes")

    if not os.path.isdir(tt_dir):
        return False, "No Traveltimes folder found — fetch travel times first"

    tt_files = sorted(
        [f for f in os.listdir(tt_dir) if f.startswith("LinkTT") and f.endswith(".csv")],
        reverse=True,
    )
    if not tt_files:
        return False, "No LinkTT CSV found in Traveltimes/"

    tt_file = os.path.join(tt_dir, tt_files[0])
    original_cwd = os.getcwd()
    os.chdir(project_dir)
    try:
        args = Namespace(target=network_dir, travel_time_file=tt_file, y=True)
        rapidx_network.fix(args)
        return True, f"Network speeds updated from {tt_files[0]}"
    except Exception as exc:
        carb.log_error(f"[network_ops] fix_network_speeds: {exc}")
        return False, f"Fix failed: {exc}"
    finally:
        os.chdir(original_cwd)
