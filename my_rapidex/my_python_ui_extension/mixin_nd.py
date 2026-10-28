"""
mixin_nd.py — Network Design Module callbacks mixed into the extension class.
"""
import os
import asyncio
import carb
import omni.usd

from my_rapidex.my_python_ui_extension.network_ops import get_city_coordinates
import my_rapidex.my_python_ui_extension.network_design as _network_design
import my_rapidex.my_python_ui_extension.traffic_visualization as _traffic_viz
import my_rapidex.my_python_ui_extension.ui_components as _ui_components


class NetworkDesignMixin:

    # ── Status / label helpers ────────────────────────────────────────────────

    def _set_nd_status(self, msg):
        try:
            self._nd_status.text = f"Network Design: {msg}"
        except Exception:
            pass
        carb.log_info(f"[network_design] {msg}")

    def _nd_update_edit_count(self):
        n = len(self._nd_edits)
        try:
            self._nd_edit_count_label.text = f"{n} road(s) modified (pending)"
        except Exception:
            pass

    def _nd_toggle_selection_mode(self):
        self._nd_selection_mode = not self._nd_selection_mode
        if self._nd_selection_mode:
            try:
                self._nd_sel_mode_btn.text = "Disable Road Selection"
                self._nd_sel_status.text   = "Selection mode: ON - click a road pipe in the viewport"
                self._nd_sel_status.style  = {"color": 0xFF44FF44}
            except Exception:
                pass
        else:
            try:
                self._nd_sel_mode_btn.text = "Enable Road Selection"
                self._nd_sel_status.text   = "Selection mode: OFF"
                self._nd_sel_status.style  = {"color": 0xFF888888}
            except Exception:
                pass

    def _nd_clear_edits(self):
        self._nd_edits.clear()
        self._nd_modified_csv = None
        try:
            self._nd_edit_count_label.text = "0 road(s) modified (pending)"
            self._nd_selected_label.text   = "No road selected"
        except Exception:
            pass
        self._set_nd_status("All edits cleared")

    # ── Stage-event / selection ───────────────────────────────────────────────

    def _nd_on_stage_event(self, event):
        if not getattr(self, "_nd_selection_mode", False):
            return
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        try:
            selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        except Exception:
            return
        for path in selected:
            prim_name = path.split("/")[-1]
            # Road edge prims are written as Line_XXXXXX under the "edges" USD group
            is_edge = (prim_name.startswith("Line_") and "/edges" in path) or \
                      prim_name.startswith("pipe_")
            if is_edge:
                try:
                    idx = int(prim_name.split("_")[1])
                except (IndexError, ValueError):
                    continue
                self._nd_selected_idx = idx
                self._run_async(self._nd_on_edge_selected_async(idx, path))
                break

    async def _nd_on_edge_selected_async(self, feature_idx, prim_path):
        loop = asyncio.get_event_loop()
        if not self._network_folder:
            return
        edges_path = os.path.join(self._network_folder, "edges.geojson")
        props = await loop.run_in_executor(
            None, lambda: _network_design.get_edge_properties(edges_path, feature_idx))
        for k, v in self._nd_edits.get(feature_idx, {}).items():
            props[k] = v
        try:
            self._nd_selected_label.text = (
                f"Selected: pipe_{feature_idx}  "
                f"({props.get('from', '?')} -> {props.get('to', '?')})")
        except Exception:
            pass
        self._nd_show_edit_panel(feature_idx, props, prim_path)

    # ── Edit panel ────────────────────────────────────────────────────────────

    def _nd_show_edit_panel(self, feature_idx, props, prim_path):
        if getattr(self, "_nd_edit_window", None):
            try:
                self._nd_edit_window.destroy()
            except Exception:
                pass
            self._nd_edit_window = None

        def _apply_cb(_fi, new_lanes, new_speed, new_type, new_capacity):
            self._nd_edits[_fi] = {
                "lanes":    new_lanes,
                "maxspeed": str(new_speed),
                "highway":  new_type,
                "capacity": new_capacity,
            }
            self._nd_update_edit_count()
            carb.log_info(
                f"[network_design] Queued edit #{_fi}: lanes={new_lanes} "
                f"speed={new_speed} capacity={new_capacity} type={new_type}"
            )

        def _remove_cb(_fi):
            self._nd_edits.pop(_fi, None)
            self._nd_update_edit_count()

        self._nd_edit_window = _ui_components.build_nd_edit_panel(
            feature_idx, props, prim_path, self._nd_edits, _apply_cb, _remove_cb)

    # ── Progress bar helpers ──────────────────────────────────────────────────

    def _nd_set_progress(self, value, visible=True):
        try:
            self._nd_progress_bar.visible = visible
            self._nd_progress_bar.model.set_value(value)
        except Exception:
            pass

    async def _nd_progress_animation(self):
        value = 0.05
        try:
            while True:
                await asyncio.sleep(3.0)
                value = min(value + 0.012, 0.92)
                self._nd_set_progress(value)
        except asyncio.CancelledError:
            pass

    # ── Run modified assignment ───────────────────────────────────────────────

    def _nd_run_modified_assignment(self):
        if not self._network_folder:
            return self._set_nd_status("Load network first")
        if not self._nd_edits:
            return self._set_nd_status("No edits pending - enable selection, click roads and apply edits first")
        if not self._rapidxpy_available:
            return self._set_nd_status("RapidXPy not available")
        _, project_dir = self._get_project_dir()
        self._set_nd_status(f"Preparing modified network ({len(self._nd_edits)} edit(s))...")
        self._run_async(self._nd_run_modified_async(project_dir))

    async def _nd_run_modified_async(self, project_dir):
        loop = asyncio.get_event_loop()

        # Read the zone data folder the user pointed to (may have Results/)
        nd_zone_folder = ""
        try:
            nd_zone_folder = (
                self._nd_zone_folder_input.model.get_value_as_string().strip().rstrip("/\\")
            )
        except Exception:
            pass

        ok, msg, nd_proj, patched_tt = await loop.run_in_executor(
            None, lambda: _network_design.prepare_modified_network(
                self._network_folder, project_dir, self._nd_edits,
                source_zone_folder=nd_zone_folder or None))
        if not ok:
            return self._set_nd_status(f"Prep failed: {msg}")
        self._set_nd_status(f"{msg} | locating OD demands file...")

        # 0. Search in the prepared Network_Design dir FIRST so RapidXPy saves results
        #    there instead of overwriting the original backup results.
        od_file = await loop.run_in_executor(
            None, lambda: _network_design.find_od_demands_file(nd_proj))

        # 1. Fallback: backup zone folder parent
        if not od_file and nd_zone_folder and os.path.isdir(nd_zone_folder):
            od_file = await loop.run_in_executor(
                None, lambda: _network_design.find_od_demands_file(
                    os.path.dirname(nd_zone_folder)))

        # 2. Fallback: search the main project directory
        if not od_file:
            od_file = await loop.run_in_executor(
                None, lambda: _network_design.find_od_demands_file(project_dir))

        # 3. Fallback: search near the volume CSV
        if not od_file:
            volume_csv = self._get_volume_csv()
            if volume_csv:
                od_file = await loop.run_in_executor(
                    None, lambda: _network_design.find_od_demands_file(
                        os.path.dirname(os.path.dirname(volume_csv))))

        if not od_file:
            return self._set_nd_status(
                "OD demands file (Elite0ODResults.csv) not found - "
                "set the Zone Data Folder in Network Design to the folder that contains Results/")

        # Use the patched LinkTT built by prepare_modified_network.
        # It is a copy of the original with updated Capacity and FFTime rows for
        # each edited edge, so the BPR cost function reflects the user's edits
        # while all unedited roads keep their original parameters.
        if patched_tt:
            self._set_nd_status(
                f"{msg} | patched LinkTT ready ({len(self._nd_edits)} edit(s)), running assignment...")
        else:
            self._set_nd_status(
                f"{msg} | no Traveltimes found — RapidXPy will derive travel times from edges.geojson...")

        nd_algo_names = ["MSA", "FW", "CFW", "BFW"]
        try:
            algo_idx  = self._nd_algorithm_selector.model.get_item_value_model().get_value_as_int()
            algorithm = nd_algo_names[algo_idx]
        except Exception:
            algorithm = "MSA"

        self._nd_set_progress(0.05)
        _prog_task = asyncio.ensure_future(self._nd_progress_animation())
        try:
            ok2, msg2, csv_path = await loop.run_in_executor(
                None, lambda: _network_design.run_modified_assignment_sync(
                    nd_proj, algorithm=algorithm,
                    od_demands_file=od_file, link_tt_file=patched_tt))
        finally:
            _prog_task.cancel()
            self._nd_set_progress(0.0, visible=False)
        if not ok2:
            return self._set_nd_status(f"Assignment failed: {msg2}")
        self._nd_modified_csv = csv_path
        self._set_nd_status(f"Done: {msg2} | use Compare Stats or Show Modified to view results")

    # ── Before / After scenario toggle ───────────────────────────────────────

    def _nd_show_original_scenario(self):
        if not self._network_folder:
            return self._set_nd_status("Load network first")
        self._set_nd_status("Applying original scenario colors...")
        self._run_async(self._nd_apply_scenario_async(use_modified=False))

    def _nd_show_modified_scenario(self):
        if not self._nd_modified_csv:
            return self._set_nd_status("Run modified assignment first")
        self._set_nd_status("Applying modified scenario colors...")
        self._run_async(self._nd_apply_scenario_async(use_modified=True))

    async def _nd_apply_scenario_async(self, use_modified):
        import omni.kit.app as _kit_app
        loop = asyncio.get_event_loop()
        city_name, project_dir = self._get_project_dir()
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        usd_dir  = os.path.join(project_dir, "Network_USD")
        if use_modified:
            volume_csv     = self._nd_modified_csv
            network_folder = os.path.join(project_dir, "Network_Design", "Network")
        else:
            volume_csv     = self._get_volume_csv()
            network_folder = self._network_folder

        # Pre-compute stats off the main thread so USD writes are the only work done on it
        _, _, pre_stats = await loop.run_in_executor(
            None, lambda: _traffic_viz.compute_traffic_stats_only(network_folder, volume_csv))

        await _kit_app.get_app().next_update_async()
        stage = omni.usd.get_context().get_stage()
        _, msg, vc_stats = _traffic_viz.run_traffic_flow(
            network_folder, volume_csv, usd_dir, stage, lat, lon,
            quartile_bounds=[
                pre_stats["q25"], pre_stats["q50"], pre_stats["q75"]
            ] if pre_stats else None)
        if vc_stats:
            self._show_traffic_flow_legend(vc_stats)
        self._set_nd_status(f"[{'Modified' if use_modified else 'Original'}] {msg}")

    # ── Before / After comparison window ─────────────────────────────────────

    def _nd_compare_before_after(self):
        if not self._nd_modified_csv:
            return self._set_nd_status("Run modified assignment first")
        self._run_async(self._nd_compare_async())

    async def _nd_compare_async(self):
        import omni.kit.app as _kit_app
        loop = asyncio.get_event_loop()
        city_name, project_dir = self._get_project_dir()
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        usd_dir  = os.path.join(project_dir, "Network_USD")

        orig_csv = self._get_volume_csv()
        mod_net  = os.path.join(project_dir, "Network_Design", "Network")

        self._set_nd_status("Computing original scenario stats...")
        _, _, orig_stats = await loop.run_in_executor(
            None, lambda: _traffic_viz.compute_traffic_stats_only(
                self._network_folder, orig_csv))

        self._set_nd_status("Computing modified scenario stats...")
        _, _, mod_stats = await loop.run_in_executor(
            None, lambda: _traffic_viz.compute_traffic_stats_only(
                mod_net, self._nd_modified_csv))

        # Apply modified-scenario colors on the main thread (USD writes)
        self._set_nd_status("Applying modified scenario colors to viewport...")
        await _kit_app.get_app().next_update_async()
        stage = omni.usd.get_context().get_stage()
        _, mod_msg, _ = _traffic_viz.run_traffic_flow(
            mod_net, self._nd_modified_csv, usd_dir, stage, lat, lon,
            quartile_bounds=[
                mod_stats["q25"], mod_stats["q50"], mod_stats["q75"]
            ] if mod_stats else None)

        if mod_stats:
            self._show_traffic_flow_legend(mod_stats)
        self._nd_show_comparison_window(orig_stats or {}, mod_stats or {}, len(self._nd_edits))
        self._set_nd_status(f"Comparison ready | {mod_msg}")

    def _nd_show_comparison_window(self, orig_stats, mod_stats, n_edits):
        if getattr(self, "_nd_compare_window", None):
            try:
                self._nd_compare_window.destroy()
            except Exception:
                pass
        self._nd_compare_window = _ui_components.build_nd_compare_window(
            orig_stats, mod_stats, n_edits)