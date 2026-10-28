"""
network_design.py
=================
Network Design Module: select roads in viewport, edit capacity properties,
rerun traffic assignment on the modified network, and compare before/after.

Public API
----------
get_edge_properties(edges_path, feature_idx)   -> dict
apply_edits_to_geojson(edges_path, edits, output_path) -> (bool, str)
prepare_modified_network(orig_net_folder, project_dir, edits)
    -> (bool, str, modified_project_dir | None)
run_modified_assignment_sync(modified_project_dir, algorithm, max_iter, rel_gap)
    -> (bool, str, csv_path | None)
"""

import os
import json
import shutil
import carb


# ── Travel-time file lookup ───────────────────────────────────────────────────

def find_link_tt_file(search_dir):
    """
    Return path to the most-recently-created LinkTT*.csv under
    *search_dir*/Traveltimes/, or None.
    """
    import glob as _glob
    tt_folder = os.path.join(search_dir, "Traveltimes")
    if not os.path.isdir(tt_folder):
        carb.log_warn(f"[network_design] Traveltimes folder not found: {tt_folder}")
        return None
    candidates = _glob.glob(os.path.join(tt_folder, "LinkTT*.csv"))
    if candidates:
        best = max(candidates, key=os.path.getctime)
        carb.log_info(f"[network_design] LinkTT file: {best}")
        return best
    carb.log_warn(f"[network_design] No LinkTT*.csv in {tt_folder}")
    return None


# ── OD demands file lookup ────────────────────────────────────────────────────

def find_od_demands_file(project_dir):
    """
    Search for Elite0ODResults.csv under any Zones* subfolder in *project_dir*.
    Returns the path to the most recently modified one, or None.
    """
    import glob as _glob
    zone_dir_names = [
        "Zones_default_HEX", "Zones_default_H3",
        "Zones_default_Square", "Zones",
    ]
    candidates = []
    for sub in zone_dir_names:
        zone_folder = os.path.join(project_dir, sub)
        if os.path.isdir(zone_folder):
            found = _glob.glob(
                os.path.join(zone_folder, "**", "Elite0ODResults.csv"), recursive=True)
            candidates.extend(found)
    # Also scan any Zones* folder found dynamically
    for zone_folder in _glob.glob(os.path.join(project_dir, "Zones*")):
        if os.path.isdir(zone_folder):
            found = _glob.glob(
                os.path.join(zone_folder, "**", "Elite0ODResults.csv"), recursive=True)
            candidates.extend(found)
    if candidates:
        best = max(set(candidates), key=os.path.getmtime)
        carb.log_info(f"[network_design] OD demands file: {best}")
        return best
    carb.log_warn(f"[network_design] Elite0ODResults.csv not found under {project_dir}")
    return None


# ── Edge property access ───────────────────────────────────────────────────────

def get_edge_properties(edges_path, feature_idx):
    """Return a copy of the properties dict for feature at *feature_idx*, or {}."""
    try:
        with open(edges_path, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])
        if 0 <= feature_idx < len(features):
            return dict(features[feature_idx].get("properties", {}))
    except Exception as exc:
        carb.log_warn(f"[network_design] get_edge_properties failed: {exc}")
    return {}


# ── GeoJSON editing ────────────────────────────────────────────────────────────

def apply_edits_to_geojson(edges_path, edits, output_path):
    """
    Write a copy of *edges_path* with *edits* applied.

    Parameters
    ----------
    edits : dict  {feature_idx (int): {property_name (str): new_value}}

    Returns
    -------
    (success: bool, message: str)
    """
    try:
        with open(edges_path, "r", encoding="utf-8") as fh:
            geojson = json.load(fh)
        features = geojson.get("features", [])
        n_edited = 0
        for fi, feat in enumerate(features):
            if fi in edits:
                props = feat.setdefault("properties", {})
                for k, v in edits[fi].items():
                    props[k] = v
                n_edited += 1
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(geojson, fh)
        carb.log_info(f"[network_design] {n_edited} edge(s) patched → {output_path}")
        return True, f"{n_edited} edge(s) modified"
    except Exception as exc:
        return False, f"GeoJSON edit failed: {exc}"


# ── Modified network setup ─────────────────────────────────────────────────────

def prepare_modified_network(original_network_folder, project_dir, edits,
                              source_zone_folder=None):
    """
    Create *project_dir/Network_Design/* with:
      Network/           — copy of original with edited edges.geojson
      Zones_*/           — copied zone folder (assignment needs it)
      Traveltimes/       — copied if present

    Parameters
    ----------
    source_zone_folder : str | None
        Explicit path to the Zones_* folder that contains a Results/ subfolder
        (e.g. from a backup/raw-data location).  When provided it takes priority
        over any zone folder found under *project_dir*.

    Returns
    -------
    (success: bool, message: str, modified_project_dir or None)
    """
    try:
        nd_proj = os.path.join(project_dir, "Network_Design")
        nd_net  = os.path.join(nd_proj, "Network")
        os.makedirs(nd_net, exist_ok=True)

        # Copy every file from the original Network folder
        for fname in os.listdir(original_network_folder):
            src = os.path.join(original_network_folder, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(nd_net, fname))

        # Overwrite with modified edges.geojson
        orig_edges = os.path.join(original_network_folder, "edges.geojson")
        mod_edges  = os.path.join(nd_net, "edges.geojson")
        ok, edit_msg = apply_edits_to_geojson(orig_edges, edits, mod_edges)
        if not ok:
            return False, edit_msg, None

        # Copy zone folder — prefer source_zone_folder (has Results/) over project_dir search
        zone_copied = False
        if source_zone_folder and os.path.isdir(source_zone_folder):
            folder_name = os.path.basename(source_zone_folder.rstrip("/\\"))
            dst_dir = os.path.join(nd_proj, folder_name)
            if not os.path.exists(dst_dir):
                shutil.copytree(source_zone_folder, dst_dir)
                carb.log_info(
                    f"[network_design] Copied source zone folder {folder_name} → Network_Design/")
            zone_copied = True

        if not zone_copied:
            zone_dir_names = [
                "Zones_default_HEX", "Zones_default_H3",
                "Zones_default_Square", "Zones",
            ]
            for sub in zone_dir_names:
                src_dir = os.path.join(project_dir, sub)
                dst_dir = os.path.join(nd_proj, sub)
                if os.path.isdir(src_dir) and not os.path.exists(dst_dir):
                    shutil.copytree(src_dir, dst_dir)
                    carb.log_info(f"[network_design] Copied {sub} → Network_Design/")
                    break

        # Copy Traveltimes — check backup parent (source_zone_folder/..) first,
        # then fall back to project_dir/Traveltimes
        tt_dst = os.path.join(nd_proj, "Traveltimes")
        tt_copied = False
        if source_zone_folder and os.path.isdir(source_zone_folder):
            tt_src_backup = os.path.join(
                os.path.dirname(source_zone_folder.rstrip("/\\")), "Traveltimes")
            if os.path.isdir(tt_src_backup) and not os.path.exists(tt_dst):
                shutil.copytree(tt_src_backup, tt_dst)
                carb.log_info(
                    f"[network_design] Copied Traveltimes from backup: {tt_src_backup}")
                tt_copied = True
        if not tt_copied:
            tt_src = os.path.join(project_dir, "Traveltimes")
            if os.path.isdir(tt_src) and not os.path.exists(tt_dst):
                shutil.copytree(tt_src, tt_dst)

        # Patch the copied LinkTT with updated capacity / FFTime for edited edges.
        # Always exclude any previous LinkTT_modified.csv so we patch from the original.
        import glob as _glob_tt
        patched_tt_path = None
        _tt_folder = os.path.join(nd_proj, "Traveltimes")
        if os.path.isdir(_tt_folder):
            _orig_candidates = [
                f for f in _glob_tt.glob(os.path.join(_tt_folder, "LinkTT*.csv"))
                if os.path.basename(f) != "LinkTT_modified.csv"
            ]
            if _orig_candidates:
                copied_tt = max(_orig_candidates, key=os.path.getctime)
                _tt_out = os.path.join(_tt_folder, "LinkTT_modified.csv")
                _ok_patch, _msg_patch, patched_tt_path = patch_link_tt_with_edits(
                    copied_tt, mod_edges, edits, _tt_out)
                if _ok_patch:
                    carb.log_info(f"[network_design] {_msg_patch}")
                else:
                    carb.log_warn(f"[network_design] {_msg_patch}")
                    patched_tt_path = None

        carb.log_info(f"[network_design] Modified project dir ready: {nd_proj}")
        return True, f"{edit_msg} | modified network prepared", nd_proj, patched_tt_path

    except Exception as exc:
        return False, f"Modified network prep failed: {exc}", None, None


# ── LinkTT patching ───────────────────────────────────────────────────────────

def patch_link_tt_with_edits(link_tt_path, edges_path, edits, output_path):
    """
    Write a patched copy of *link_tt_path* where each row matching an edited
    edge gets updated Capacity and free-flow travel time (FFTime).

    Matching is done by (from_node, to_node) looked up from edges.geojson.
    Original files are never modified; only *output_path* is written.

    Returns (success: bool, message: str, output_path or None)
    """
    import csv as _csv
    try:
        with open(edges_path, "r", encoding="utf-8") as fh:
            features = json.load(fh).get("features", [])

        edge_lookup = {}  # (str_from, str_to) -> (edit_dict, props_dict)
        for fi, edit in edits.items():
            if 0 <= fi < len(features):
                props  = features[fi].get("properties", {})
                a_node = str(props.get("from", props.get("u", ""))).strip()
                b_node = str(props.get("to",   props.get("v", ""))).strip()
                if a_node and b_node:
                    edge_lookup[(a_node, b_node)] = (edit, props)

        with open(link_tt_path, "r", encoding="utf-8", newline="") as fh:
            reader     = _csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows       = list(reader)

        if not fieldnames:
            return False, "LinkTT CSV has no header", None

        _fn_lower = {c.lower(): c for c in fieldnames}
        a_col   = next((v for k, v in _fn_lower.items()
                        if k in ("a", "from", "fromnode", "from_node", "orig")), None)
        b_col   = next((v for k, v in _fn_lower.items()
                        if k in ("b", "to", "tonode", "to_node", "dest")), None)
        cap_col = next((v for k, v in _fn_lower.items() if "cap" in k), None)
        tt_col  = next((v for k, v in _fn_lower.items()
                        if k in ("fftime", "fftt", "fft", "traveltime", "freeflowtime",
                                 "freeflowttime")
                        or ("time" in k and "free" in k)), None)

        carb.log_info(
            f"[network_design] LinkTT cols: A={a_col} B={b_col} "
            f"cap={cap_col} tt={tt_col} | all={fieldnames}"
        )

        n_patched = 0
        for row in rows:
            if not (a_col and b_col):
                break
            key = (str(row.get(a_col, "")).strip(), str(row.get(b_col, "")).strip())
            if key not in edge_lookup:
                continue
            edit, props = edge_lookup[key]

            if cap_col and "capacity" in edit:
                try:
                    row[cap_col] = str(float(edit["capacity"]))
                except (ValueError, TypeError):
                    pass

            if tt_col and "maxspeed" in edit:
                try:
                    speed_kmh = float(edit["maxspeed"])
                    length_m  = float(props.get("length", props.get("Length", 0)) or 0)
                    if speed_kmh > 0 and length_m > 0:
                        row[tt_col] = str(round((length_m / 1000.0 / speed_kmh) * 60.0, 6))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

            n_patched += 1

        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        carb.log_info(f"[network_design] LinkTT patched: {n_patched} link(s) → {output_path}")
        return True, f"Patched {n_patched} link(s) in LinkTT", output_path

    except Exception as exc:
        carb.log_warn(f"[network_design] patch_link_tt_with_edits: {exc}")
        return False, f"LinkTT patch failed: {exc}", None


# ── Python executable lookup ───────────────────────────────────────────────────

def _find_python_exe():
    import shutil as _shutil
    candidates = [
        "C:/users/sakshi/miniforge3/envs/rapidxpy/python.exe",
        "C:/isaac-sim/kit/python/python.exe",
        "C:/isaac-sim/python/python.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    found = _shutil.which("python")
    if found:
        return found
    import sys
    return sys.executable


# ── Modified assignment ────────────────────────────────────────────────────────

def run_modified_assignment_sync(modified_project_dir, algorithm="MSA",
                                  max_iter=1000, rel_gap=0.0001,
                                  od_demands_file=None, link_tt_file=None):
    """
    Run traffic assignment on *modified_project_dir* using RapidXPy in a
    subprocess so that plan.Assign's sys.exit() cannot kill Isaac Sim.

    Returns (success: bool, message: str, csv_path or None)
    """
    import subprocess as _sp
    import tempfile

    _ext_root = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    ).replace("\\", "/")

    cpu = max(1, (os.cpu_count() or 4) - 2)
    od_repr  = repr(od_demands_file)   # 'None' or "'path...'"
    tt_repr  = repr(link_tt_file)

    script = f"""
import sys, os
sys.path.insert(0, {_ext_root!r})
os.chdir({modified_project_dir!r})

from argparse import Namespace
from rapidxpy.scripts import plan as _plan

assign_args = Namespace(
    algorithm={algorithm!r},
    relGap={rel_gap},
    maxIterations={max_iter},
    capA=[0.6, 0.61, 0.1],
    capF=[0.7, 0.71, 0.1],
    ffTF=[1, 1.01, 0.1],
    demF=[1.0, 1.01, 0.1],
    zoneFolder=None,
    odDemandsfile={od_repr},
    linkTTfile={tt_repr},
    cpu={cpu},
    y=True, verbose=False,
    proportions=False,
    otpdir=None, otpdeparturedate=None, otpdeparturetime=None,
)
_plan.Assign(assign_args)
print("ASSIGNMENT_COMPLETE")
"""

    python_exe = _find_python_exe()
    carb.log_info(f"[network_design] Assignment subprocess python: {python_exe}")
    carb.log_info(f"[network_design] Working dir: {modified_project_dir}")
    carb.log_info(f"[network_design] OD file: {od_demands_file}")
    carb.log_info(f"[network_design] LinkTT : {link_tt_file}")

    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="rapidx_assign_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(script)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        result = _sp.run(
            [python_exe, script_path],
            capture_output=True, timeout=3600,
            cwd=modified_project_dir, env=env,
        )
        _stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        _stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

        for i in range(0, max(len(_stdout), 1), 2000):
            chunk = _stdout[i:i + 2000]
            if chunk.strip():
                carb.log_info(f"[network_design] {chunk}")
        for i in range(0, max(len(_stderr), 1), 2000):
            chunk = _stderr[i:i + 2000]
            if chunk.strip():
                carb.log_warn(f"[network_design] {chunk}")

        if result.returncode != 0:
            err = (_stderr or _stdout)[-800:]
            return False, f"Assignment subprocess exited {result.returncode}:\n{err}", None

    except _sp.TimeoutExpired:
        return False, "Assignment timed out (1 hour)", None
    except Exception as exc:
        return False, f"Assignment subprocess error: {exc}", None
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass

    # Only search inside modified_project_dir — od_demands_file should now be
    # sourced from the copied Network_Design zone folder, so results land here.
    _result_names = {"Elite0LinkResults.csv", "LinkResults.csv"}
    candidates = []
    for dirpath, _, files in os.walk(modified_project_dir):
        for fname in files:
            if fname in _result_names:
                candidates.append(os.path.join(dirpath, fname))
    carb.log_info(f"[network_design] Result CSV candidates under {modified_project_dir}: {candidates}")

    if not candidates:
        return False, "Assignment subprocess completed but no result CSV found", None

    best = max(candidates, key=os.path.getmtime)
    carb.log_info(f"[network_design] Modified result CSV: {best}")
    return True, f"Modified assignment done -> {os.path.basename(best)}", best