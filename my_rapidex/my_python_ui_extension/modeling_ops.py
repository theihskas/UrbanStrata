"""
modeling_ops.py
===============
Wrapper functions for RapidXPy modeling tools (planning, accessibility).
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