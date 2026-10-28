"""
analysis_ops.py
===============
Tutorial 5-style traffic analysis functions (Gini accessibility, CO2 emissions, Noise Leq).
"""

import os
import glob as _glob
import csv as _csv
import json as _json
import math as _math
import carb

try:
    import numpy as np
except ImportError:
    np = None

import my_rapidex.my_python_ui_extension.traffic_visualization as _traffic_viz

def compute_gini_accessibility(project_dir):
    if np is None:
        return False, "numpy not available"

    patterns = [
        os.path.join(project_dir, "**", "ODTravelTimes.csv"),
        os.path.join(project_dir, "**", "ODTT*.csv"),
        os.path.join(project_dir, "**", "EliteODTT*.csv"),
    ]
    found = []
    for p in patterns:
        found.extend(_glob.glob(p, recursive=True))
    if not found:
        return False, "No OD travel time CSV found - run assignment first"
        
    found.sort(key=os.path.getmtime, reverse=True)
    ff_times, cong_times = {}, {}
    try:
        with open(found[0], newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                od   = (row.get("Origin", ""), row.get("Destination", ""))
                ff   = float(row.get("FFT",    row.get("FreeflowTT", 0)) or 0)
                cong = float(row.get("CongTT", row.get("TT", 0))         or 0)
                if ff > 0 and cong > 0:
                    ff_times[od]   = ff
                    cong_times[od] = cong
    except Exception as exc:
        return False, f"CSV parse error: {exc}"
        
    if not ff_times:
        return False, "Could not parse OD travel times (check column names: Origin, Destination, FFT, CongTT)"

    ODCI = {od: cong_times[od] / ff_times[od] for od in ff_times if od in cong_times}
    zones = set(od[0] for od in ODCI) | set(od[1] for od in ODCI)

    def _zone_avg(idx):
        return {z: np.mean([ODCI[od] for od in ODCI if od[idx] == z]) for z in zones
                if any(od[idx] == z for od in ODCI)}

    zone_oci = _zone_avg(0)
    zone_dci = _zone_avg(1)

    def gini(arr):
        arr = np.array(arr, dtype=float)
        if np.mean(arr) == 0:
            return 0.0
        total = sum(np.sum(np.abs(xi - arr[i:])) for i, xi in enumerate(arr[:-1], 1))
        return total / (len(arr) ** 2 * np.mean(arr))

    g_oci = gini(list(zone_oci.values())) if zone_oci else 0.0
    g_dci = gini(list(zone_dci.values())) if zone_dci else 0.0
    msg = f"Gini OCI={g_oci:.4f}  DCI={g_dci:.4f}  ({len(zones)} zones, {len(ODCI)} OD pairs)"
    carb.log_info(f"[analysis] {msg}")
    return True, msg

def compute_emissions_analysis(project_dir, network_folder, results_folder):
    if not network_folder:
        return False, "Load network first"
    edges_path = os.path.join(network_folder, "edges.geojson")
    if not os.path.exists(edges_path):
        return False, "edges.geojson not found"
        
    with open(edges_path, "r", encoding="utf-8") as fh:
        features = _json.load(fh).get("features", [])

    volume_csv = _traffic_viz.find_estimate_csv(results_folder or project_dir)
    volume_map = _traffic_viz.read_volume_csv(volume_csv) if volume_csv else {}

    # Tutorial 5 exponential CO2 formula (g/mile -> kg)
    b0, b1, b2, b3, b4 = 7.613, -0.138565, 0.0039151, -0.000049519, 0.0000002386

    def link_co2(length_m, fft_min, flow):
        if fft_min <= 0 or flow <= 0:
            return 0.0
        speed_ms  = length_m / (fft_min * 60)
        speed_mph = min(speed_ms * 2.23694, 100.0)
        co2_g_mi  = _math.exp(
            b0 + b1*speed_mph + b2*speed_mph**2
            + b3*speed_mph**3 + b4*speed_mph**4)
        return co2_g_mi * (length_m * 0.000621371) * flow / 1000.0  # kg

    total_co2, rows = 0.0, []
    for feat in features:
        props   = feat.get("properties", {})
        from_id = str(int(props.get("from", 0)))
        to_id   = str(int(props.get("to",   0)))
        vol     = volume_map.get((from_id, to_id), 0.0)
        length  = float(props.get("length", 100))
        fft     = float(props.get("FFT", props.get("fft", 1)) or 1)
        co2     = link_co2(length, fft, vol)
        total_co2 += co2
        rows.append((from_id, to_id, round(co2, 4)))

    out = os.path.join(project_dir, "emissions_results.csv")
    with open(out, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["From", "To", "CO2_kg"])
        w.writerows(rows)
        
    msg = f"Total CO₂: {total_co2:.1f} kg  |  {len(rows)} links  |  saved: emissions_results.csv"
    carb.log_info(f"[analysis] {msg}")
    return True, msg

def compute_noise_analysis(project_dir, network_folder, results_folder):
    if np is None:
        return False, "numpy not available"
    if not network_folder:
        return False, "Load network first"
    edges_path = os.path.join(network_folder, "edges.geojson")
    if not os.path.exists(edges_path):
        return False, "edges.geojson not found"
        
    with open(edges_path, "r", encoding="utf-8") as fh:
        features = _json.load(fh).get("features", [])

    volume_csv = _traffic_viz.find_estimate_csv(results_folder or project_dir)
    volume_map = _traffic_viz.read_volume_csv(volume_csv) if volume_csv else {}

    L0 = 37.3
    noise_vals, rows = [], []
    for feat in features:
        props   = feat.get("properties", {})
        from_id = str(int(props.get("from", 0)))
        to_id   = str(int(props.get("to",   0)))
        flow    = volume_map.get((from_id, to_id), 0.0)
        length  = float(props.get("length", 100))
        fft     = float(props.get("FFT", props.get("fft", 1)) or 1)
        if flow <= 0 or fft <= 0:
            leq = 0.0
        else:
            speed_kmh = min((length / (fft * 60)) * 3.6, 100.0)
            lcar  = 27.7 + 10 * np.log10(1 + (0.02 * speed_kmh) ** 3)
            lhg   = 23.1 + 12.5 * np.log10(max(speed_kmh, 1.0))
            f2    = 10 ** (0.1 * (lhg - lcar))
            k2    = 2
            denom = 100 + 8.23 * k2
            speedC = lcar - L0 + 10 * np.log10((100 + (f2 - 1)) * k2) / denom
            leq   = L0 + 10 * np.log10(flow * (1 + 0.082 * 3)) + speedC
        noise_vals.append(leq)
        rows.append((from_id, to_id, round(leq, 2)))

    out = os.path.join(project_dir, "noise_results.csv")
    with open(out, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["From", "To", "Leq_dB"])
        w.writerows(rows)
        
    active = [v for v in noise_vals if v > 0]
    msg = (f"Noise avg={np.mean(active):.1f} dB  max={max(active):.1f} dB  "
           f"| {len(active)} active links  |  saved: noise_results.csv") if active else "No traffic volumes found"
    carb.log_info(f"[analysis] {msg}")
    return True, msg