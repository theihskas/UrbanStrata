"""
modeling_ops.py
===============
Wrapper functions for RapidXPy modeling tools (planning, transit, accessibility).
Removes deep internal rapidxpy dependencies from extension.py.
"""
import os
import carb
from argparse import Namespace

def run_pcu_estimation(city_dir, args):
    try:
        from rapidxpy.scripts import plan as _plan
    except ImportError:
        return False, "RapidXPy not available"
    cwd = os.getcwd()
    os.chdir(city_dir)
    try:
        _plan.Estimation(args)
        return True, "PCU estimation completed"
    except Exception as exc:
        carb.log_error(f"[PCU] {exc}")
        return False, f"PCU estimation failed: {exc}"
    finally:
        os.chdir(cwd)

def run_assignment(project_dir, args):
    try:
        from rapidxpy.scripts import plan as _plan
    except ImportError:
        return False, "RapidXPy not available"
    cwd = os.getcwd()
    os.chdir(project_dir)
    try:
        _plan.Assign(args)
        return True, f"{args.algorithm} assignment completed"
    except Exception as exc:
        carb.log_error(f"[Assign] {exc}")
        return False, f"Assignment failed: {exc}"
    finally:
        os.chdir(cwd)

def build_otp_graph(otp_dir):
    try:
        from rapidxpy.scripts import transit as _transit
        _transit.build_graph(Namespace(otp_dir=otp_dir, verbose=True))
        return True, "OTP graph built"
    except Exception as exc:
        return False, f"Build failed: {exc}"

def start_otp_server(otp_dir):
    try:
        from rapidxpy.scripts.transit_functions import start_otp_server_background
        return True, "OTP server started", start_otp_server_background(otp_dir)
    except Exception as exc:
        return False, f"Start failed: {exc}", None

def stop_otp_server(process):
    try:
        from rapidxpy.scripts.transit_functions import stop_otp_server as _stop
        _stop(process)
        return True, "OTP server stopped"
    except Exception as exc:
        return False, f"Stop failed: {exc}"

def run_transit_zone_analysis(args):
    try:
        from rapidxpy.scripts import transit as _transit
        _transit.zone_analysis(args)
        return True, "Zone transit analysis complete"
    except Exception as exc:
        return False, f"Zone analysis failed: {exc}"

def run_transit_point_analysis(args):
    try:
        from rapidxpy.scripts import transit as _transit
        _transit.point_analysis(args)
        return True, "Point transit analysis complete"
    except Exception as exc:
        return False, f"Point analysis failed: {exc}"

def run_decompose(project_dir, args):
    try:
        from rapidxpy.scripts import plan as _plan
        cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            _plan.Decompose(args)
            return True, "Decomposition complete"
        finally:
            os.chdir(cwd)
    except Exception as exc:
        return False, f"Decompose failed: {exc}"

def run_robustness(project_dir):
    try:
        from rapidxpy.scripts.core import robustness as _robustness
        cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            _robustness.robustness(zoneFolder=None, network_folder=os.path.join(project_dir, "Network"), verbose=False)
            return True, "Robustness analysis complete"
        finally:
            os.chdir(cwd)
    except Exception as exc:
        return False, f"Robustness failed: {exc}"

def run_gtfs_metrics(gtfs_folder, zones_geojson):
    try:
        from rapidxpy.scripts.core import multi_modal as _multimodal
        metrics = _multimodal.calculate_gtfs_zone_metrics(gtfs_folder, zones_geojson)
        return True, f"GTFS metrics computed for {len(metrics)} zones"
    except Exception as exc:
        return False, f"GTFS metrics failed: {exc}"

def run_mode_split(project_dir, gtfs_folder, zones_geojson):
    try:
        from rapidxpy.scripts.core import multi_modal as _multimodal
        _multimodal.run_mode_split(zones_geojson=zones_geojson, gtfs_folder=gtfs_folder if os.path.isdir(gtfs_folder) else None, output_folder=os.path.join(project_dir, "ModeSplit"))
        return True, "Mode split complete"
    except Exception as exc:
        return False, f"Mode split failed: {exc}"

def run_accessibility(project_dir, zones, centroids, api):
    try:
        from rapidxpy.scripts.core import accessibility as _accessibility
        cwd = os.getcwd()
        os.chdir(project_dir)
        try:
            _accessibility.accessibility_analysis(region_pois=None, newzonesgdf=zones, zones=zones, centroids=centroids, api=api, output=os.path.join(project_dir, "accessibility_results.json"))
            return True, "Accessibility analysis complete"
        finally:
            os.chdir(cwd)
    except Exception as exc:
        return False, f"Accessibility failed: {exc}"