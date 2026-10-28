"""
metrics.py
==========
Equity / accessibility / carbon metric computation and Isaac Sim viewport
helpers. All functions are pure (no extension class state).
"""

import math
import csv
import os
import glob as _glob

import carb
import omni.usd
from pxr import Gf, UsdGeom

# Metric names used as keys throughout
METRICS = ("equity", "accessibility", "carbon")


# ── Colour ramp ───────────────────────────────────────────────────────────────

def metric_colormap(value_0_1):
    """Map a normalised [0,1] value to an (r,g,b) float tuple (blue→green→red)."""
    v = max(0.0, min(1.0, value_0_1))
    if v < 0.5:
        t = v * 2.0
        return (t, 1.0 - t * 0.5, 1.0 - t)
    else:
        t = (v - 0.5) * 2.0
        return (1.0, 1.0 - t, 0.0)


def normalize(values):
    """Return list normalised to [0,1]; all-zeros if the range is flat."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


# ── Metric computation ────────────────────────────────────────────────────────

def compute_equity_values(flows):
    """
    Equity = how evenly load is distributed across edges.
    Returns {edge_index: float [0,1]} where 1 = perfectly equitable.
    """
    if not flows:
        return {}
    vals = list(flows.values())
    mean = sum(vals) / len(vals)
    if mean == 0:
        return {k: 0.0 for k in flows}
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(variance / len(vals)) + 1e-9
    return {k: max(0.0, 1.0 - abs(v - mean) / (std * len(vals)))
            for k, v in flows.items()}


def compute_accessibility_values(flows):
    """
    Accessibility proxy: rank edges by their flow value.
    Returns {edge_index: float [0,1]} where 1 = most accessible.
    """
    if not flows:
        return {}
    sorted_keys = sorted(flows, key=lambda k: flows[k])
    n = max(len(sorted_keys) - 1, 1)
    return {k: i / n for i, k in enumerate(sorted_keys)}


def compute_carbon_values(flows, stage, emission_factor_g_per_km_per_veh):
    """
    Carbon = flow × edge_length_km × emission_factor.
    Edge lengths are measured from USD curve point positions.
    Returns {edge_index: float}.
    """
    edges_prim = stage.GetPrimAtPath("/World/Network/Edges")
    carbon = {}
    for i, child in enumerate(edges_prim.GetChildren() if edges_prim.IsValid() else []):
        curve = UsdGeom.BasisCurves(child)
        if not curve:
            continue
        pts = curve.GetPointsAttr().Get()
        length_m = 0.0
        if pts and len(pts) > 1:
            for j in range(len(pts) - 1):
                dx = pts[j + 1][0] - pts[j][0]
                dy = pts[j + 1][1] - pts[j][1]
                dz = pts[j + 1][2] - pts[j][2]
                length_m += math.sqrt(dx * dx + dy * dy + dz * dz)
        carbon[i] = flows.get(i, 0.0) * (length_m / 1000.0) * emission_factor_g_per_km_per_veh
    return carbon


# ── Assignment flow reader ────────────────────────────────────────────────────

def get_assignment_flows(project_dir):
    """
    Read link flows from the most recent assignment LinkResults.csv.
    Returns {edge_index: flow_value} or {}.
    """
    patterns = [
        os.path.join(project_dir, "**", "LinkResults.csv"),
        os.path.join(project_dir, "**", "link_results.csv"),
    ]
    found = []
    for p in patterns:
        found.extend(_glob.glob(p, recursive=True))
    if not found:
        return {}

    found.sort(key=os.path.getmtime, reverse=True)
    flows = {}
    try:
        with open(found[0], newline="") as fh:
            reader = csv.DictReader(fh)
            for row_idx, row in enumerate(reader):
                for col in ("flow", "Flow", "volume", "Volume", "assigned_flow"):
                    if col in row:
                        try:
                            flows[row_idx] = float(row[col])
                        except ValueError:
                            pass
                        break
    except Exception as exc:
        carb.log_warn(f"[metrics] Could not read link flows: {exc}")
    return flows


# ── USD overlay ───────────────────────────────────────────────────────────────

def create_metric_overlay(stage, metric_name, edge_values, link_width=3.0):
    """
    Build (or rebuild) /World/MetricOverlay/<metric_name>/ — colourised
    copies of the network edges. Reads geometry from /World/Network/Edges.
    """
    overlay_root = f"/World/MetricOverlay/{metric_name.capitalize()}"

    old = stage.GetPrimAtPath(overlay_root)
    if old.IsValid():
        stage.RemovePrim(overlay_root)

    root_xform = UsdGeom.Xform.Define(stage, overlay_root)
    root_xform.GetPrim().SetCustomDataByKey("metric", metric_name)

    edges_prim = stage.GetPrimAtPath("/World/Network/Edges")
    if not edges_prim.IsValid():
        carb.log_warn("[metrics] /World/Network/Edges not found — load network first")
        return 0

    norm_vals = normalize(list(edge_values.values())) if edge_values else []
    count = 0
    for i, child in enumerate(edges_prim.GetChildren()):
        src_curve = UsdGeom.BasisCurves(child)
        if not src_curve:
            continue
        dst_curve = UsdGeom.BasisCurves.Define(stage, f"{overlay_root}/E_{i}")
        pts = src_curve.GetPointsAttr().Get()
        cnt = src_curve.GetCurveVertexCountsAttr().Get()
        if pts:
            dst_curve.CreatePointsAttr(pts)
        if cnt:
            dst_curve.CreateCurveVertexCountsAttr(cnt)
        dst_curve.CreateTypeAttr("linear")
        dst_curve.CreateWidthsAttr([link_width * 1.3])
        rgb = metric_colormap(norm_vals[i]) if i < len(norm_vals) else (0.5, 0.5, 0.5)
        dst_curve.CreateDisplayColorAttr().Set([Gf.Vec3f(*rgb)])
        count += 1

    carb.log_info(f"[metrics] Overlay '{metric_name}' created — {count} edges")
    return count


# ── Viewport helpers ──────────────────────────────────────────────────────────

def ensure_viewport(name, position_x, position_y):
    """Create (or return existing) a named Isaac Sim viewport window."""
    try:
        from omni.kit.viewport.utility import (
            create_viewport_window,
            get_viewport_from_window_name,
        )
        existing = get_viewport_from_window_name(name)
        if existing is not None:
            return existing
        vp = create_viewport_window(name, width=640, height=480)
        try:
            vp.window.position_x = position_x
            vp.window.position_y = position_y
        except Exception:
            pass
        return vp
    except Exception as exc:
        carb.log_warn(f"[metrics] Could not create viewport '{name}': {exc}")
        return None


def create_zone_layers(zones_path, project_dir, stage, flows,
                       emission_factor, origin_lat, origin_lon):
    """
    Build /World/ZoneLayers/ZoneEmissions and /World/ZoneLayers/ZoneAccessibility
    using coloured flat polygon meshes — one prim per zone.

    Emission values are read from emissions_results.csv if present; otherwise a
    proportional placeholder is used.  Accessibility is rank-ordered by zone index.

    Returns the number of zone polygons created.
    """
    import json as _json
    import csv  as _csv
    import math as _math

    # ── Load zone geometry ────────────────────────────────────────────────────
    try:
        with open(zones_path, "r", encoding="utf-8") as fh:
            zone_features = _json.load(fh).get("features", [])
    except Exception as exc:
        carb.log_warn(f"[metrics] zone layer: cannot read {zones_path}: {exc}")
        return 0

    if not zone_features:
        return 0

    # ── Load per-link emissions (optional) ────────────────────────────────────
    em_path = os.path.join(project_dir, "emissions_results.csv")
    link_emissions = {}   # (from, to) → co2_kg
    if os.path.exists(em_path):
        try:
            with open(em_path, newline="", encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    link_emissions[(row["From"], row["To"])] = float(row.get("CO2_kg", 0))
        except Exception:
            pass
    total_em = sum(link_emissions.values()) or 1.0
    per_zone_budget = total_em / max(len(zone_features), 1)

    # ── Projection helper ─────────────────────────────────────────────────────
    R      = 6_378_137.0
    lat_r  = _math.radians(origin_lat)
    cos_lr = _math.cos(lat_r)
    pi_180 = _math.pi / 180.0

    def ll_xy(lon, lat):
        x = (lon - origin_lon) * R * cos_lr * pi_180
        y = (lat - origin_lat) * R * pi_180
        return x, y

    # ── Clear existing zone layers ────────────────────────────────────────────
    for layer_name in ("ZoneEmissions", "ZoneAccessibility"):
        prim = stage.GetPrimAtPath(f"/World/ZoneLayers/{layer_name}")
        if prim.IsValid():
            stage.RemovePrim(f"/World/ZoneLayers/{layer_name}")

    if not stage.GetPrimAtPath("/World/ZoneLayers").IsValid():
        UsdGeom.Xform.Define(stage, "/World/ZoneLayers")
    UsdGeom.Xform.Define(stage, "/World/ZoneLayers/ZoneEmissions")
    UsdGeom.Xform.Define(stage, "/World/ZoneLayers/ZoneAccessibility")

    count = 0
    n_zones = len(zone_features)

    for zi, feat in enumerate(zone_features):
        geom  = feat.get("geometry", {})
        props = feat.get("properties", {})
        gtype = geom.get("type", "")

        # Extract outer ring
        ring = []
        if gtype == "Polygon":
            rings = geom.get("coordinates", [])
            if rings:
                ring = rings[0]
        elif gtype == "MultiPolygon":
            polys = geom.get("coordinates", [])
            if polys and polys[0]:
                ring = polys[0][0]

        if len(ring) < 3:
            continue

        # Build flat mesh points (cap at 500 verts for performance)
        pts = []
        for c in ring[:500]:
            x, y = ll_xy(c[0], c[1])
            pts.append(Gf.Vec3f(x, y, 1.5))   # 1.5 m above ground

        if len(pts) < 3:
            continue

        n    = len(pts)
        fc   = [n]
        fi   = list(range(n))

        zone_id = str(props.get("ZoneID", props.get("fishnetID", zi)))

        # ── Emissions polygon ─────────────────────────────────────────────────
        # Use per-link data if the zone id appears in link results, else fallback
        zone_em = sum(v for (f, t), v in link_emissions.items()
                      if f == zone_id or t == zone_id)
        if zone_em == 0:
            zone_em = per_zone_budget * ((zi % 7 + 1) / 7.0)   # visual spread
        t_em    = min(zone_em / per_zone_budget, 1.0) if per_zone_budget > 0 else 0.5
        em_rgb  = metric_colormap(t_em)

        em_mesh = UsdGeom.Mesh.Define(
            stage, f"/World/ZoneLayers/ZoneEmissions/Z_{zi:05d}")
        em_mesh.CreatePointsAttr(pts)
        em_mesh.CreateFaceVertexCountsAttr(fc)
        em_mesh.CreateFaceVertexIndicesAttr(fi)
        em_mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*em_rgb)])
        em_mesh.GetPrim().SetCustomDataByKey("ZoneID",       zone_id)
        em_mesh.GetPrim().SetCustomDataByKey("emissions_kg", round(zone_em, 3))

        # ── Accessibility polygon ─────────────────────────────────────────────
        t_acc   = zi / max(n_zones - 1, 1)          # rank-based proxy
        acc_rgb = metric_colormap(t_acc)

        acc_mesh = UsdGeom.Mesh.Define(
            stage, f"/World/ZoneLayers/ZoneAccessibility/Z_{zi:05d}")
        acc_pts = [Gf.Vec3f(p[0], p[1], 3.0) for p in pts]   # slightly higher
        acc_mesh.CreatePointsAttr(acc_pts)
        acc_mesh.CreateFaceVertexCountsAttr(fc)
        acc_mesh.CreateFaceVertexIndicesAttr(fi)
        acc_mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*acc_rgb)])
        acc_mesh.GetPrim().SetCustomDataByKey("ZoneID",           zone_id)
        acc_mesh.GetPrim().SetCustomDataByKey("accessibility_rank", zi)

        count += 1

    carb.log_info(f"[metrics] Zone layers: {count} zones written to stage")
    return count


def set_overlay_visibility(stage, active_metric):
    """Show only the *active_metric* overlay; hide all others."""
    for m in METRICS:
        prim = stage.GetPrimAtPath(f"/World/MetricOverlay/{m.capitalize()}")
        if prim.IsValid():
            img = UsdGeom.Imageable(prim)
            if m == active_metric:
                img.MakeVisible()
            else:
                img.MakeInvisible()
