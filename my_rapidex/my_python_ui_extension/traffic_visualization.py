"""
traffic_visualization.py
========================
Animated traffic flow in road pipe meshes + volume-styled intersection nodes.
Adapted from create_traffic_flow_in_pipes2.py and create_animated_nodes.py
(D:/dresden-digital-twin). All geometry constants are tunable.

Public entry points:
  run_traffic_flow(network_folder, volume_csv, usd_dir, stage, origin_lat, origin_lon)
  run_animated_nodes(network_folder, volume_csv, usd_dir, stage)
  add_road_volume_labels(network_folder, volume_csv, stage, origin_lat, origin_lon)
  read_volume_csv(csv_path) -> dict  {(from_str, to_str): volume}
  find_estimate_csv(results_folder) -> str | None
"""

import gc
import json
import math
import os
import time as _time

import carb
import omni.usd
from pxr import Gf, Sdf, UsdGeom, UsdShade, UsdLux, Vt

# ── Defaults ─────────────────────────────────────────────────────────────────
FPS              = 12.0
DURATION_SECONDS = 3.0
TOTAL_FRAMES     = int(FPS * DURATION_SECONDS)   # 36
MAX_PARTICLES    = 6
MIN_PARTICLES    = 1
VOLUME_DIVISOR   = 150.0
PARTICLE_RADIUS  = 1.5
EMISSIVE_INTENSITY = 5.0
PIPE_EMISSIVE    = 0.4
MIN_NODE_HEIGHT  = 4.0
MAX_NODE_HEIGHT  = 100.0
NODE_HEIGHT_BASE = 20.0
USE_LOG_SCALE    = True
DEFAULT_SPEED_KMPH = 50.0


# ── Colour helpers ────────────────────────────────────────────────────────────

def vc_ratio_to_color(vc):
    t = max(vc, 0.0)
    if t < 0.4:
        f = t / 0.4
        return Gf.Vec3f(0.1 + f * 0.5, 0.9, 0.1)
    elif t < 0.7:
        f = (t - 0.4) / 0.3
        return Gf.Vec3f(0.6 + f * 0.4, 0.9 - f * 0.2, 0.1)
    elif t < 1.0:
        f = (t - 0.7) / 0.3
        return Gf.Vec3f(1.0, 0.7 - f * 0.4, 0.1 - f * 0.05)
    return Gf.Vec3f(1.0, 0.05, 0.0)


def vc_ratio_to_speed(vc):
    t = max(vc, 0.0)
    if t < 0.4:   return 2.0
    elif t < 0.7: return 1.2
    elif t < 1.0: return 0.6
    return 0.25


def _parse_maxspeed(val):
    """Parse OSM maxspeed string to km/h float.  Handles '50', '30 mph', None."""
    if val is None:
        return DEFAULT_SPEED_KMPH
    s = str(val).strip().lower()
    try:
        if "mph" in s:
            return float(s.replace("mph", "").strip()) * 1.60934
        return float(s.replace("km/h", "").replace("kph", "").strip())
    except (ValueError, TypeError):
        return DEFAULT_SPEED_KMPH


def maxspeed_to_color(speed_kmh):
    """Colour ramp: green (slow/residential) -> yellow -> orange -> red (fast/motorway)."""
    t = max(0.0, min(1.0, (speed_kmh - 20.0) / 110.0))   # 20-130 km/h
    if t < 0.33:
        f = t / 0.33
        return Gf.Vec3f(0.10 + f * 0.80, 0.80 + f * 0.05, 0.10)   # green->yellow
    elif t < 0.66:
        f = (t - 0.33) / 0.33
        return Gf.Vec3f(0.90 + f * 0.10, 0.85 - f * 0.40, 0.05)   # yellow->orange
    else:
        f = (t - 0.66) / 0.34
        return Gf.Vec3f(1.00, 0.45 - f * 0.40, 0.05)               # orange->red


def maxspeed_to_anim_speed(speed_kmh):
    """
    Animation speed multiplier from posted speed limit.
    Reference: 50 km/h -> 1.0 (one road traversal per DURATION_SECONDS).
    Motorway (130 km/h) -> 2.6×, residential (20 km/h) -> 0.4×.
    """
    return max(0.3, min(3.5, speed_kmh / DEFAULT_SPEED_KMPH))


def volume_to_color(t):
    t = max(0.0, min(float(t), 1.0))
    if t < 0.35:
        f = t / 0.35
        return Gf.Vec3f(0.1 + f * 0.5, 0.95, 0.1)
    elif t < 0.65:
        f = (t - 0.35) / 0.30
        return Gf.Vec3f(0.6 + f * 0.4, 0.95 - f * 0.25, 0.1)
    elif t < 0.90:
        f = (t - 0.65) / 0.25
        return Gf.Vec3f(1.0, 0.70 - f * 0.45, 0.1 - f * 0.05)
    f = (t - 0.90) / 0.10
    return Gf.Vec3f(1.0, 0.25 - f * 0.20, 0.05)


# ── Quartile color palette (Q1=light-green → Q4=dark-red) ────────────────────
_QUARTILE_COLORS_GF = [
    Gf.Vec3f(0.20, 0.80, 0.20),   # Q1 — light green  (lowest V/C %)
    Gf.Vec3f(0.80, 0.80, 0.00),   # Q2 — yellow
    Gf.Vec3f(1.00, 0.50, 0.00),   # Q3 — orange
    Gf.Vec3f(0.85, 0.03, 0.03),   # Q4 — dark red      (highest V/C %)
]
# ABGR format for omni.ui (0xAABBGGRR)
QUARTILE_ABGR = [
    0xFF33CC33,   # Q1 light green
    0xFF00CCCC,   # Q2 yellow
    0xFF0080FF,   # Q3 orange
    0xFF0808DD,   # Q4 dark red
]


def compute_vc_stats(vc_pct_list):
    """
    Descriptive statistics for a list of V/C % values.
    Returns dict: mean, median, mode, q25, q50, q75, min, max, count.
    Mode is rounded to the nearest 5 % for readability.
    """
    if not vc_pct_list:
        return {}
    from collections import Counter
    vals = sorted(vc_pct_list)
    n    = len(vals)
    mean_v   = sum(vals) / n
    median_v = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    rounded  = [round(v / 5.0) * 5.0 for v in vals]
    mode_v   = float(Counter(rounded).most_common(1)[0][0])
    q25 = vals[max(0, int(n * 0.25))]
    q50 = vals[max(0, int(n * 0.50))]
    q75 = vals[max(0, int(n * 0.75))]
    return {
        "mean":   round(mean_v, 1),
        "median": round(median_v, 1),
        "mode":   mode_v,
        "q25":    round(q25, 1),
        "q50":    round(q50, 1),
        "q75":    round(q75, 1),
        "min":    round(vals[0], 1),
        "max":    round(vals[-1], 1),
        "count":  n,
    }


# ── Data helpers ──────────────────────────────────────────────────────────────

def read_volume_csv(csv_path):
    """Load Elite0LinkResults.csv → {(from_str, to_str): volume}."""
    import csv as _csv
    _FROM = ["from", "fromnodeid", "from_node", "init_node", "a", "tail"]
    _TO   = ["to",   "tonodeid",   "to_node",   "term_node", "b", "head"]
    _VOL  = ["volume", "flow", "assigned_volume", "assigned_flow",
             "veh", "count", "pcu", "link_flow", "q"]
    vmap = {}
    try:
        with open(csv_path, "r", encoding="utf-8") as fh:
            reader = _csv.DictReader(fh)
            cols   = {c.strip().lower(): c for c in (reader.fieldnames or [])}
            fc = next((cols[k] for k in _FROM if k in cols), None)
            tc = next((cols[k] for k in _TO   if k in cols), None)
            vc = next((cols[k] for k in _VOL  if k in cols), None)
            if not fc or not tc or not vc:
                carb.log_warn(f"[traffic_viz] CSV missing from/to/volume cols; "
                              f"got: {list(cols.keys())}")
                return vmap
            for row in reader:
                try:
                    key = (str(int(float(row[fc]))), str(int(float(row[tc]))))
                    vmap[key] = float(row[vc])
                except (ValueError, KeyError):
                    pass
    except Exception as exc:
        carb.log_warn(f"[traffic_viz] volume CSV not loaded: {exc}")
    return vmap


def find_estimate_csv(results_folder):
    """
    Search results_folder recursively for Elite0LinkResults.csv.
    Returns the most recently modified one, or None.
    """
    import glob as _glob
    candidates = _glob.glob(
        os.path.join(results_folder, "**", "Elite0LinkResults.csv"), recursive=True
    ) + _glob.glob(
        os.path.join(results_folder, "**", "LinkResults.csv"), recursive=True
    )
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def compute_traffic_stats_only(network_folder, volume_csv):
    """
    Compute V/C statistics for a scenario WITHOUT touching the USD stage.
    Safe to call from a background thread / executor.
    Returns (ok: bool, message: str, vc_stats: dict or None).
    """
    edges_path = os.path.join(network_folder, "edges.geojson")
    if not os.path.exists(edges_path):
        return False, f"edges.geojson not found in {network_folder}", None

    volume_map = read_volume_csv(volume_csv) if volume_csv and os.path.exists(volume_csv) else {}

    try:
        with open(edges_path, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Failed to read edges.geojson: {exc}", None

    vc_list = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "LineString":
            continue
        props   = feat.get("properties", {})
        from_id = str(int(props.get("from", 0)))
        to_id   = str(int(props.get("to",   0)))
        vol     = volume_map.get((from_id, to_id), 0.0)
        if vol <= 0:
            continue
        lanes = max(1, int(props.get("lanes", 1) or 1))
        try:
            capacity = max(1.0, float(props["capacity"])) if props.get("capacity") else lanes * 1800
        except (ValueError, TypeError):
            capacity = lanes * 1800
        vc_list.append((vol / capacity) * 100.0)

    vc_stats = compute_vc_stats(vc_list)
    if not vc_stats:
        return False, "No V/C data — check volume CSV matches network edges", None

    nodes_path = os.path.join(network_folder, "nodes.geojson")
    try:
        with open(nodes_path, "r", encoding="utf-8") as fh:
            vc_stats["num_nodes"] = len(json.load(fh).get("features", []))
    except Exception:
        vc_stats["num_nodes"] = 0
    vc_stats["num_roads"] = len(features)

    return True, f"Stats computed: {vc_stats['count']} roads with volume data", vc_stats


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _lonlat_to_xyz(lon, lat, origin_lon, origin_lat, h=0.0):
    import math as _math
    try:
        lon, lat, h = float(lon), float(lat), float(h)
        if not (_math.isfinite(lon) and _math.isfinite(lat)):
            return (0.0, 0.0, 0.0)
    except (ValueError, TypeError):
        return (0.0, 0.0, 0.0)
    R = 6_378_137.0
    lat_r = _math.radians(origin_lat)
    x = (lon - origin_lon) * R * _math.cos(lat_r) * _math.pi / 180.0
    y = (lat - origin_lat) * R * _math.pi / 180.0
    return (x, y, h)


def _path_length(pts):
    total = 0.0
    for i in range(len(pts) - 1):
        dx = pts[i+1][0]-pts[i][0]
        dy = pts[i+1][1]-pts[i][1]
        dz = pts[i+1][2]-pts[i][2]
        total += math.sqrt(dx*dx + dy*dy + dz*dz)
    return total


def _point_at_distance(pts, target_d):
    acc = 0.0
    for i in range(len(pts) - 1):
        dx = pts[i+1][0]-pts[i][0]
        dy = pts[i+1][1]-pts[i][1]
        dz = pts[i+1][2]-pts[i][2]
        seg = math.sqrt(dx*dx + dy*dy + dz*dz)
        if seg < 1e-9:
            continue
        if acc + seg >= target_d:
            t = (target_d - acc) / seg
            return (float(pts[i][0]+t*dx), float(pts[i][1]+t*dy), float(pts[i][2]+t*dz))
        acc += seg
    p = pts[-1]
    return (float(p[0]), float(p[1]), float(p[2]))


# ── OmniPBR material helper ───────────────────────────────────────────────────

def _make_material(stage, mat_path, color, emissive_intensity, opacity=1.0,
                   is_particle=False):
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
    sh.CreateInput("emissive_intensity",     Sdf.ValueTypeNames.Float).Set(emissive_intensity)
    if not is_particle:
        sh.CreateInput("opacity_constant",   Sdf.ValueTypeNames.Float).Set(opacity)
        sh.CreateInput("enable_opacity",     Sdf.ValueTypeNames.Bool).Set(True)
    mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    return mat


# ── Per-frame particle animator ───────────────────────────────────────────────

class TrafficFlowAnimator:
    """
    Moves PointInstancer particles along road centerlines every Kit frame.

    Uses the Kit update-event stream instead of a USD animation sublayer so
    that RTX never receives a bulk time-sample flood that overflows the TLAS.
    The subscription keeps itself alive as long as this object is alive; call
    stop() (or delete the object) to cancel.
    """

    def __init__(self, pos_attr, all_particles, duration_sec):
        self._pos_attr  = pos_attr
        self._particles = all_particles      # list of (cl, l, speed_mult, offset_frac)
        self._duration  = duration_sec
        self._start     = _time.monotonic()
        self._is_stopped = False
        import omni.kit.app as _app
        self._sub = _app.get_app().get_update_event_stream().create_subscription_to_pop(
            self._tick, name="rapidx_traffic_flow")
        carb.log_info(f"[traffic_viz] Animator started — {len(all_particles)} particles")

    def _tick(self, _event):
        # Early exit if already stopped to prevent further updates
        if self._is_stopped:
            return
        
        t = (_time.monotonic() - self._start) % self._duration
        positions = []
        for cl, l, speed_mult, offset_frac in self._particles:
            progress = ((t * speed_mult + offset_frac * self._duration)
                         % self._duration) / self._duration
            try:
                p = _point_at_distance(cl, progress * l)
            except Exception:
                p = (0.0, 0.0, 0.0)
            positions.append(Gf.Vec3f(*p))
        try:
            self._pos_attr.Set(Vt.Vec3fArray(positions))
        except Exception:
            self.stop()

    def stop(self):
        """Properly stop the animator and unsubscribe from update events."""
        self._is_stopped = True
        if self._sub is not None:
            try:
                # Clear the subscription reference to allow garbage collection
                self._sub = None
            except Exception as e:
                carb.log_warn(f"[traffic_viz] Error stopping animator: {e}")
        carb.log_info("[traffic_viz] Animator stopped and unsubscribed")


# ── Traffic flow in pipes ─────────────────────────────────────────────────────

def run_traffic_flow(network_folder, volume_csv, usd_dir, stage, origin_lat, origin_lon,
                     max_edges=0, use_maxspeed=False, quartile_bounds=None):
    """
    Color road pipe meshes by V/C ratio quartile.
    Q1 (lowest load) = light green … Q4 (highest load) = dark red.

    V/C % = (volume / capacity) × 100, where capacity = lanes × 1800 veh/hr.

    quartile_bounds : optional [q1|q2, q2|q3, q3|q4] split values in V/C %.
                      If None, splits are derived from the dataset's own quartiles.

    Returns (ok: bool, message: str, vc_stats: dict or None).
    vc_stats keys: mean, median, mode, q25, q50, q75, min, max, count,
                   q1_bound, q2_bound, q3_bound.
    """
    carb.log_info("[traffic_viz] Coloring pipes by V/C ratio quartile...")

    edges_path = os.path.join(network_folder, "edges.geojson")
    if not os.path.exists(edges_path):
        return False, f"edges.geojson not found in {network_folder}", None

    volume_map = read_volume_csv(volume_csv) if volume_csv and os.path.exists(volume_csv) else {}
    carb.log_info(f"[traffic_viz] {len(volume_map)} link volumes loaded")

    with open(edges_path, "r", encoding="utf-8") as fh:
        features = json.load(fh).get("features", [])
    carb.log_info(f"[traffic_viz] {len(features)} edge features")

    num_nodes = 0
    nodes_path = os.path.join(network_folder, "nodes.geojson")
    if os.path.exists(nodes_path):
        try:
            with open(nodes_path, "r", encoding="utf-8") as fh:
                num_nodes = len(json.load(fh).get("features", []))
        except Exception:
            pass

    est_stats = {
        "total_demand": "N/A", "tstt": "N/A", "avg_time": "N/A",
        "congestion": "N/A", "weighted_link_cong": "N/A",
        "weighted_vc": "N/A", "vkt": "N/A"
    }
    if volume_csv and os.path.exists(volume_csv):
        _vol_dir    = os.path.dirname(volume_csv)
        _parent_dir = os.path.dirname(_vol_dir)
        # Search order: same dir → Estimation/ subdir → parent's Estimation/ subdir
        _est_candidates = [
            os.path.join(_vol_dir,    "EstimationResults.csv"),
            os.path.join(_vol_dir,    "Estimation", "EstimationResults.csv"),
            os.path.join(_parent_dir, "Estimation", "EstimationResults.csv"),
            os.path.join(_parent_dir, "EstimationResults.csv"),
        ]
        est_res_path = next((p for p in _est_candidates if os.path.exists(p)), None)
        if est_res_path:
            carb.log_info(f"[traffic_viz] EstimationResults.csv found: {est_res_path}")
            try:
                import csv as _csv
                with open(est_res_path, 'r', encoding='utf-8') as f:
                    reader = list(_csv.DictReader(f))
                    if reader:
                        last_row = reader[-1]
                        def _g(row, *keys):
                            for k in keys:
                                v = row.get(k)
                                if v is not None and str(v).strip():
                                    try:
                                        return f"{float(v):.4f}"
                                    except (ValueError, TypeError):
                                        return str(v).strip()
                            return "N/A"
                        est_stats["total_demand"]      = _g(last_row, "Total Demand", "TotalDemand")
                        est_stats["tstt"]              = _g(last_row, "TSTT")
                        est_stats["avg_time"]          = _g(last_row, "Average Time (min)")
                        est_stats["congestion"]        = _g(last_row, "Congestion (%)")
                        est_stats["weighted_link_cong"]= _g(last_row, "Weighted Link Congestion")
                        est_stats["weighted_vc"]       = _g(last_row, "Weighted VC Ratio")
                        est_stats["vkt"]               = _g(last_row, "Vehicle Kilometres Travelled")
            except Exception as exc:
                carb.log_warn(f"[traffic_viz] Could not read EstimationResults.csv: {exc}")
        else:
            carb.log_warn(f"[traffic_viz] EstimationResults.csv not found near {volume_csv}")

    # ── Find existing pipe group ──────────────────────────────────────────────
    edges_group = None
    for try_path in ["/World/edges", "/CesiumGeoreference/edges",
                     "/World/CesiumGeoreference/edges"]:
        p = stage.GetPrimAtPath(try_path)
        if p and p.IsValid():
            edges_group = p
            break
    if not edges_group:
        return False, "No /World/edges prim — run Load Network first", None

    pipe_prims = {}
    for child in edges_group.GetChildren():
        if child.GetTypeName() == "Mesh":
            try:
                idx = int(child.GetName().split("_")[1])
                pipe_prims[idx] = child
            except (IndexError, ValueError):
                pass
    carb.log_info(f"[traffic_viz] {len(pipe_prims)} pipe meshes found")

    MATS = "/World/TrafficMaterials"
    if not stage.GetPrimAtPath(MATS):
        UsdGeom.Xform.Define(stage, MATS)

    # ── Compute V/C % per edge ────────────────────────────────────────────────
    # Capacity = lanes × 1800 veh/hr  (HCM simplified urban arterial model)
    vc_per_edge = {}   # feature_idx -> V/C %
    vc_list     = []

    for fi, feat in enumerate(features):
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "LineString":
            continue
        props   = feat.get("properties", {})
        from_id = str(int(props.get("from", 0)))
        to_id   = str(int(props.get("to",   0)))
        vol     = volume_map.get((from_id, to_id), 0.0)
        if vol <= 0:
            continue
        lanes = max(1, int(props.get("lanes", 1) or 1))
        try:
            capacity = max(1.0, float(props["capacity"])) if props.get("capacity") else lanes * 1800
        except (ValueError, TypeError):
            capacity = lanes * 1800
        vc_pct = (vol / capacity) * 100.0
        vc_per_edge[fi] = vc_pct
        vc_list.append(vc_pct)

    vc_stats = compute_vc_stats(vc_list)
    if not vc_stats:
        return False, "No V/C data — check volume CSV matches network edges", None
        
    vc_stats["num_roads"] = len(features)
    vc_stats["num_nodes"] = num_nodes
    vc_stats.update(est_stats)

    # ── Determine quartile split boundaries (V/C %) ───────────────────────────
    if quartile_bounds and len(quartile_bounds) >= 3:
        q1_b, q2_b, q3_b = (float(quartile_bounds[0]),
                             float(quartile_bounds[1]),
                             float(quartile_bounds[2]))
    else:
        q1_b = vc_stats["q25"]
        q2_b = vc_stats["q50"]
        q3_b = vc_stats["q75"]
    vc_stats.update({"q1_bound": q1_b, "q2_bound": q2_b, "q3_bound": q3_b})

    # ── Color each pipe by its V/C quartile ───────────────────────────────────
    quartile_mats = {}
    colored = 0

    for fi, feat in enumerate(features):
        vc_pct = vc_per_edge.get(fi, -1.0)
        if vc_pct < 0:
            continue   # no volume data — leave pipe with existing material
        q_idx = 3 if vc_pct >= q3_b else (2 if vc_pct >= q2_b else
                                           (1 if vc_pct >= q1_b else 0))
        pp = pipe_prims.get(fi)
        if pp:
            if q_idx not in quartile_mats:
                quartile_mats[q_idx] = _make_material(
                    stage, f"{MATS}/VCQuartile_{q_idx}",
                    _QUARTILE_COLORS_GF[q_idx], PIPE_EMISSIVE)
            UsdShade.MaterialBindingAPI(pp).Bind(quartile_mats[q_idx])
            colored += 1

    msg = (f"Traffic flow: {colored} roads colored by V/C quartile "
           f"| Q1≤{q1_b:.1f}%  Q2≤{q2_b:.1f}%  Q3≤{q3_b:.1f}% "
           f"| mean {vc_stats['mean']:.1f}%  max {vc_stats['max']:.1f}%")
    carb.log_info(f"[traffic_viz] {msg}")
    return True, msg, vc_stats


# ── Animated intersection nodes ───────────────────────────────────────────────

def run_animated_nodes(network_folder, volume_csv, usd_dir, stage, centroid=False):
    """
    Style intersection node cylinders by volume (height + colour).
    Writes a defs layer to usd_dir/node_defs.usda and adds it as a sublayer.

    If centroid=True, uses larger size range for centroid markers.
    """
    nodes_path = os.path.join(network_folder, "nodes.geojson")
    if not os.path.exists(nodes_path):
        return False, f"nodes.geojson not found in {network_folder}"

    volume_map = read_volume_csv(volume_csv) if volume_csv and os.path.exists(volume_csv) else {}
    # Aggregate volumes per node (inflow + outflow)
    node_volumes = {}
    for (f_id, t_id), vol in volume_map.items():
        node_volumes[f_id] = node_volumes.get(f_id, 0.0) + vol
        node_volumes[t_id] = node_volumes.get(t_id, 0.0) + vol

    with open(nodes_path, "r", encoding="utf-8") as fh:
        node_features = json.load(fh).get("features", [])

    # Centroid markers use larger height range so they stand out
    min_h = 8.0  if centroid else MIN_NODE_HEIGHT
    max_h = 200.0 if centroid else MAX_NODE_HEIGHT

    vol_vals = [v for v in node_volumes.values() if v > 0]
    if vol_vals:
        vol_min, vol_max = min(vol_vals), max(vol_vals)
        log_min = math.log1p(vol_min) if USE_LOG_SCALE else vol_min
        log_max = math.log1p(vol_max) if USE_LOG_SCALE else vol_max
    else:
        vol_min = vol_max = log_min = log_max = 0.0

    # Find nodes group
    nodes_group = None
    for try_path in ["/World/nodes", "/CesiumGeoreference/nodes", "/World/CesiumGeoreference/nodes"]:
        p = stage.GetPrimAtPath(try_path)
        if p and p.IsValid():
            nodes_group = p
            break
    if not nodes_group:
        return False, "No /World/nodes prim — run Load Network first"

    node_prims = {}
    for child in nodes_group.GetChildren():
        name = child.GetName()
        if name.startswith("Pt_"):
            try:
                node_prims[int(name[3:])] = child
            except ValueError:
                pass

    os.makedirs(usd_dir, exist_ok=True)
    defs_file  = os.path.join(usd_dir, "node_defs.usda").replace("\\", "/")
    root_layer = stage.GetRootLayer()
    while defs_file in root_layer.subLayerPaths:
        root_layer.subLayerPaths.remove(defs_file)

    defs_layer = Sdf.Layer.Find(defs_file)
    if defs_layer:
        defs_layer.Clear()
    else:
        if os.path.exists(defs_file):
            os.remove(defs_file)
        defs_layer = Sdf.Layer.CreateNew(defs_file)

    bound_count = 0
    with Sdf.ChangeBlock():
        mats_spec = Sdf.PrimSpec(defs_layer, "NodeMaterials", Sdf.SpecifierDef)
        mats_spec.typeName = "Scope"

        for feat_idx, feature in enumerate(node_features):
            geom = feature.get("geometry")
            if not geom or geom.get("type") != "Point":
                continue
            props   = feature.get("properties", {})
            node_id = str(int(props.get("NodeID", props.get("osmid", feat_idx))))
            vol     = node_volumes.get(node_id, 0.0)
            if vol <= 0:
                continue

            if USE_LOG_SCALE and log_max > log_min:
                t = (math.log1p(vol) - log_min) / (log_max - log_min)
            elif vol_max > vol_min:
                t = (vol - vol_min) / (vol_max - vol_min)
            else:
                t = 0.5
            t = max(0.0, min(t, 1.0))

            color = volume_to_color(t)
            sz    = (min_h + (max_h - min_h) * t) / NODE_HEIGHT_BASE

            cyl_prim = node_prims.get(feat_idx)
            if not cyl_prim:
                continue

            # Create material
            mat_name = f"Node_{feat_idx:06d}_Mat"
            mat_spec    = Sdf.PrimSpec(mats_spec, mat_name, Sdf.SpecifierDef)
            mat_spec.typeName = "Material"
            sh_spec = Sdf.PrimSpec(mat_spec, "Shader", Sdf.SpecifierDef)
            sh_spec.typeName = "Shader"
            Sdf.AttributeSpec(sh_spec, "info:implementationSource",
                              Sdf.ValueTypeNames.Token,
                              Sdf.VariabilityUniform).default = "sourceAsset"
            Sdf.AttributeSpec(sh_spec, "info:mdl:sourceAsset",
                              Sdf.ValueTypeNames.Asset,
                              Sdf.VariabilityUniform).default = Sdf.AssetPath("OmniPBR.mdl")
            Sdf.AttributeSpec(sh_spec, "info:mdl:sourceAsset:subIdentifier",
                              Sdf.ValueTypeNames.Token,
                              Sdf.VariabilityUniform).default = "OmniPBR"
            Sdf.AttributeSpec(sh_spec, "inputs:diffuse_color_constant",
                              Sdf.ValueTypeNames.Color3f).default = color
            sh_out = Sdf.Path(str(mat_spec.path) + "/Shader.outputs:out")
            Sdf.AttributeSpec(sh_spec, "outputs:out",     Sdf.ValueTypeNames.Token)
            Sdf.AttributeSpec(sh_spec, "outputs:surface", Sdf.ValueTypeNames.Token)
            surf = Sdf.AttributeSpec(mat_spec, "outputs:surface", Sdf.ValueTypeNames.Token)
            surf.connectionPathList.explicitItems = [sh_out]
            surf_mdl = Sdf.AttributeSpec(mat_spec, "outputs:mdl:surface", Sdf.ValueTypeNames.Token)
            surf_mdl.connectionPathList.explicitItems = [sh_out]

            # Over the cylinder prim
            parts = str(cyl_prim.GetPath()).strip("/").split("/")
            over  = defs_layer
            for seg in parts:
                path_so_far = ("/" if isinstance(over, Sdf.Layer) else str(over.path)) + seg
                existing = defs_layer.GetPrimAtPath(path_so_far)
                if existing:
                    over = existing
                elif isinstance(over, Sdf.Layer):
                    over = Sdf.PrimSpec(over, seg, Sdf.SpecifierOver)
                else:
                    over = Sdf.PrimSpec(over, seg, Sdf.SpecifierOver)

            mat_rel = Sdf.RelationshipSpec(over, "material:binding", False)
            mat_rel.targetPathList.explicitItems = [
                Sdf.Path(f"/NodeMaterials/{mat_name}")
            ]
            op_order = Sdf.AttributeSpec(over, "xformOpOrder",
                                          Sdf.ValueTypeNames.TokenArray,
                                          Sdf.VariabilityUniform)
            op_order.default = Vt.TokenArray(["xformOp:scale"])
            scale_attr = Sdf.AttributeSpec(over, "xformOp:scale",
                                            Sdf.ValueTypeNames.Double3)
            scale_attr.default = Gf.Vec3d(1.0, 1.0, sz)
            bound_count += 1

    defs_layer.Save()
    del defs_layer
    gc.collect()
    root_layer.subLayerPaths.insert(0, defs_file)
    label = "centroid" if centroid else "intersection"
    return True, f"Styled {bound_count} {label} nodes  |  layer: {defs_file}"


# ── Floating road volume labels (viewport overlay text) ──────────────────────

def collect_volume_label_data(network_folder, volume_csv, origin_lat, origin_lon,
                               max_labels=200):
    """
    Collect road midpoint positions and volume text for viewport overlay labels.

    Returns (success, message, label_data) where label_data is a list of
    (x, y, z, text) tuples in local ENU metres — pass to build_volume_overlay().
    """
    edges_path = os.path.join(network_folder, "edges.geojson")
    if not os.path.exists(edges_path):
        return False, f"edges.geojson not found in {network_folder}", []

    volume_map = read_volume_csv(volume_csv) if volume_csv and os.path.exists(volume_csv) else {}
    try:
        with open(edges_path, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
    except Exception as exc:
        return False, f"Cannot read edges.geojson: {exc}", []

    vol_vals = [v for v in volume_map.values() if v > 0]
    vol_max  = max(vol_vals) if vol_vals else 1.0

    # Sort by volume descending so busiest roads get labels first
    scored = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        props   = feat.get("properties", {})
        from_id = str(int(props.get("from", 0)))
        to_id   = str(int(props.get("to", 0)))
        vol     = volume_map.get((from_id, to_id), 0.0)
        scored.append((vol, feat, coords))

    scored.sort(key=lambda x: -x[0])
    scored = scored[:max_labels]

    label_data = []
    for vol, feat, coords in scored:
        mid_c = coords[len(coords) // 2]
        mx, my, mz = _lonlat_to_xyz(mid_c[0], mid_c[1], origin_lon, origin_lat,
                                     mid_c[2] if len(mid_c) > 2 else 0.0)
        mz += 18.0  # float 18 m above road
        text = f"{int(vol):,} veh" if vol > 0 else "0 veh"
        label_data.append((mx, my, mz, text))

    n_with_vol = sum(1 for v, _, _ in scored if v > 0)
    msg = (f"Road labels: {len(label_data)} floating text labels "
           f"({n_with_vol} with volume data)")
    return True, msg, label_data


def build_volume_overlay(label_data):
    """
    Create viewport scene overlay with floating text labels at ENU positions.

    Returns the SceneView object — caller MUST keep it alive (store on self).
    Returns None if the viewport API is unavailable.
    """
    try:
        import omni.kit.viewport.utility as _vp_util
        import omni.ui as _ui
        from omni.ui import scene as _sc

        vp_win = _vp_util.get_active_viewport_window()
        if not vp_win:
            carb.log_warn("[traffic_viz] No active viewport window for overlay labels")
            return None

        with vp_win.get_frame("rapidx_volume_labels"):
            sv = _sc.SceneView()
            with sv.scene:
                for x, y, z, text in label_data:
                    with _sc.Transform(
                        transform=_sc.Matrix44.get_translation_matrix(x, y, z)
                    ):
                        _sc.Label(
                            text,
                            alignment=_ui.Alignment.CENTER,
                            size=10,
                            color=0xFFFFFFDD,
                        )
        carb.log_info(f"[traffic_viz] Overlay: {len(label_data)} floating labels built")
        return sv
    except Exception as exc:
        carb.log_warn(f"[traffic_viz] Overlay labels failed: {exc}")
        return None


def add_road_volume_labels(network_folder, volume_csv, usd_dir, stage,
                            origin_lat, origin_lon):
    """
    Build floating text volume labels in the viewport overlay.
    usd_dir and stage kept for API compatibility but no USD is written.
    Returns (success, message, scene_view_or_None).
    """
    ok, msg, label_data = collect_volume_label_data(
        network_folder, volume_csv, origin_lat, origin_lon)
    if not ok:
        return False, msg, None
    sv = build_volume_overlay(label_data)
    return True, msg, sv
