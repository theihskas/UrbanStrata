"""
mixin_analysis.py — Noise analysis, traffic viz and pre-computed noise callbacks.
"""
import os
import asyncio
import carb
import omni.usd

from my_rapidex.my_python_ui_extension.network_ops import get_city_coordinates
from my_rapidex.my_python_ui_extension.noise_ops import compute_noise_decay, find_elite_csv
from my_rapidex.my_python_ui_extension.noise_viz import (
    apply_noise_shell_visualization, apply_noise_shell_from_precomputed,
    update_shell_opacity, apply_noise_cartographic,
)
import my_rapidex.my_python_ui_extension.traffic_visualization as _traffic_viz
import my_rapidex.my_python_ui_extension.analysis_ops as _analysis_ops
import my_rapidex.my_python_ui_extension.ui_components as _ui_components

try:
    from my_rapidex.my_python_ui_extension.noise_raster import (
        create_noise_raster_geotiff, apply_noise_raster_overlay,
    )
    _RASTER_OK = True
except ImportError:
    _RASTER_OK = False


class AnalysisMixin:

    # ── Traffic visualisation ─────────────────────────────────────────────────

    def _get_volume_csv(self):
        _, project_dir = self._get_project_dir()
        results_folder = self._viz_results_folder_input.model.get_value_as_string().strip()
        time_folder    = self._viz_time_folder_input.model.get_value_as_string().strip()
        if results_folder and results_folder.lower().endswith(".csv") and os.path.isfile(results_folder):
            return results_folder
        if results_folder and time_folder:
            search_root = os.path.join(results_folder, time_folder)
        elif results_folder:
            search_root = results_folder
        else:
            search_root = project_dir
        csv_path = _traffic_viz.find_estimate_csv(search_root)
        if not csv_path:
            csv_path = _traffic_viz.find_estimate_csv(project_dir)
        return csv_path

    def _animate_traffic_flow(self):
        if not self._network_folder:
            return self._set_traffic_viz_status("Load network first")
        self._set_traffic_viz_status("Animating traffic flow...")
        self._run_async(self._animate_traffic_flow_async())

    async def _animate_traffic_flow_async(self):
        import omni.kit.app
        loop = asyncio.get_event_loop()
        city_name, project_dir = self._get_project_dir()
        lat, lon   = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        usd_dir    = os.path.join(project_dir, "Network_USD")
        volume_csv = self._get_volume_csv()
        if getattr(self, "_traffic_animator", None):
            self._traffic_animator.stop()
            self._traffic_animator = None
        await omni.kit.app.get_app().next_update_async()
        stage = omni.usd.get_context().get_stage()
        _, msg, vc_stats = _traffic_viz.run_traffic_flow(
            self._network_folder, volume_csv, usd_dir, stage, lat, lon)
        if vc_stats:
            self._show_traffic_flow_legend(vc_stats)
        self._set_traffic_viz_status(msg)

    async def _recolor_traffic_vc_async(self, quartile_bounds):
        import omni.kit.app
        loop = asyncio.get_event_loop()
        city_name, project_dir = self._get_project_dir()
        lat, lon   = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        usd_dir    = os.path.join(project_dir, "Network_USD")
        volume_csv = self._get_volume_csv()
        await omni.kit.app.get_app().next_update_async()
        stage = omni.usd.get_context().get_stage()
        self._set_traffic_viz_status("Re-coloring by custom V/C splits...")
        _, msg, vc_stats = _traffic_viz.run_traffic_flow(
            self._network_folder, volume_csv, usd_dir, stage, lat, lon,
            quartile_bounds=quartile_bounds)
        if vc_stats:
            self._show_traffic_flow_legend(vc_stats)
        self._set_traffic_viz_status(msg)

    def _style_nodes(self):
        if not self._network_folder:
            return self._set_traffic_viz_status("Load network first")
        _, project_dir = self._get_project_dir()
        usd_dir    = os.path.join(project_dir, "Network_USD")
        stage      = omni.usd.get_context().get_stage()
        volume_csv = self._get_volume_csv()
        self._set_traffic_viz_status("Styling intersection nodes...")
        _, msg = _traffic_viz.run_animated_nodes(
            self._network_folder, volume_csv, usd_dir, stage, centroid=False)
        self._set_traffic_viz_status(msg)

    def _style_centroids(self):
        if not self._network_folder:
            return self._set_traffic_viz_status("Load network first")
        _, project_dir = self._get_project_dir()
        usd_dir    = os.path.join(project_dir, "Network_USD")
        stage      = omni.usd.get_context().get_stage()
        volume_csv = self._get_volume_csv()
        self._set_traffic_viz_status("Styling centroid markers...")
        _, msg = _traffic_viz.run_animated_nodes(
            self._network_folder, volume_csv, usd_dir, stage, centroid=True)
        self._set_traffic_viz_status(msg)

    def _add_volume_labels(self):
        if not self._network_folder:
            return self._set_traffic_viz_status("Load network first")
        city_name, project_dir = self._get_project_dir()
        lat, lon   = get_city_coordinates(city_name)
        usd_dir    = os.path.join(project_dir, "Network_USD")
        stage      = omni.usd.get_context().get_stage()
        volume_csv = self._get_volume_csv()
        self._set_traffic_viz_status("Building volume text labels...")
        _, msg, sv = _traffic_viz.add_road_volume_labels(
            self._network_folder, volume_csv, usd_dir, stage, lat, lon)
        self._volume_label_overlay = sv
        self._set_traffic_viz_status(msg)

    # ── Legend helpers ────────────────────────────────────────────────────────

    def _show_traffic_flow_legend(self, vc_stats=None):
        if getattr(self, "_traffic_legend_window", None):
            try:
                self._traffic_legend_window.destroy()
            except Exception:
                pass
            self._traffic_legend_window = None
        self._traffic_legend_window = _ui_components.build_traffic_flow_legend(
            vc_stats, lambda bounds: self._run_async(self._recolor_traffic_vc_async(bounds)))

    def _show_noise_legend(self, status_msg=""):
        if getattr(self, "_noise_legend_window", None):
            try:
                self._noise_legend_window.visible = True
                return
            except Exception:
                pass
        self._noise_legend_window = _ui_components.build_noise_legend(status_msg)

    def _show_cartographic_legend(self, status_msg=""):
        if getattr(self, "_carto_legend_window", None):
            try:
                self._carto_legend_window.visible = True
                return
            except Exception:
                pass
        self._carto_legend_window = _ui_components.build_cartographic_legend(status_msg)

    # ── Tutorial-5 analysis ───────────────────────────────────────────────────

    def _compute_gini_accessibility(self):
        _, project_dir = self._get_project_dir()
        ok, msg = _analysis_ops.compute_gini_accessibility(project_dir)
        self._analysis_status.text = msg

    def _compute_emissions_analysis(self):
        _, project_dir = self._get_project_dir()
        results_folder = self._viz_results_folder_input.model.get_value_as_string().strip()
        ok, msg = _analysis_ops.compute_emissions_analysis(project_dir, self._network_folder, results_folder)
        self._analysis_status.text = msg

    def _compute_noise_analysis(self):
        _, project_dir = self._get_project_dir()
        results_folder = self._viz_results_folder_input.model.get_value_as_string().strip()
        ok, msg = _analysis_ops.compute_noise_analysis(project_dir, self._network_folder, results_folder)
        self._analysis_status.text = msg

    # ── 3D Noise Decay ────────────────────────────────────────────────────────

    def _compute_noise_decay_3d(self):
        city_name, project_dir = self._get_project_dir()
        elite_csv = self._elite_csv_input.model.get_value_as_string().strip()
        if not elite_csv:
            found = find_elite_csv(project_dir)
            if not found:
                self._analysis_status.text = "Enter the Elite0LinkResults.csv path above."
                return
            elite_csv = found
        elif not os.path.exists(elite_csv):
            self._analysis_status.text = f"Elite CSV not found: {elite_csv}"
            return

        edges_override = self._edges_geojson_input.model.get_value_as_string().strip()
        if edges_override and not os.path.exists(edges_override):
            self._analysis_status.text = f"edges.geojson not found: {edges_override}"
            return

        if not edges_override:
            search = os.path.dirname(elite_csv)
            for _ in range(8):
                candidate = os.path.join(search, "Network", "edges.geojson")
                if os.path.exists(candidate):
                    project_dir = search
                    break
                search = os.path.dirname(search)

        noise_out = ""
        try:
            noise_out = self._noise_output_folder_input.model.get_value_as_string().strip()
        except Exception:
            pass
        self._analysis_status.text = "Computing noise decay (background)..."
        self._run_async(self._compute_noise_decay_3d_async(
            city_name, project_dir, elite_csv, edges_override or None, noise_out or None))

    async def _compute_noise_decay_3d_async(self, city_name, project_dir, elite_csv,
                                             edges_override, noise_output_folder=None):
        loop = asyncio.get_event_loop()
        output_dir = noise_output_folder if noise_output_folder else os.path.join(project_dir, "Noise")
        ok, msg, _ = await loop.run_in_executor(
            None, lambda: compute_noise_decay(
                city_name, project_dir,
                elite_csv=elite_csv,
                edges_geojson=edges_override,
                output_dir=output_dir))
        if not ok:
            self._analysis_status.text = msg
            return
        self._analysis_status.text = f"{msg} - geocoding..."
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        self._analysis_status.text = f"{msg} - building shell visualization..."
        link_noise_path = os.path.join(output_dir, "link_noise.geojson")
        stage = omni.usd.get_context().get_stage()
        ok2, load_msg = await apply_noise_shell_visualization(
            link_noise_path, lat, lon, output_dir, stage)
        self._analysis_status.text = (
            f"{msg} | {load_msg}" if ok2 else f"{msg} | Shell viz failed: {load_msg}")
        if ok2:
            self._show_noise_legend(load_msg)

    # ── Pre-computed noise ────────────────────────────────────────────────────

    def _visualise_precomputed_noise(self):
        link_noise_path  = self._link_noise_input.model.get_value_as_string().strip()
        decay_bands_path = self._decay_bands_input.model.get_value_as_string().strip()
        if not link_noise_path:
            self._analysis_status.text = "Set link_noise.geojson path above."
            return
        if not decay_bands_path:
            self._analysis_status.text = "Set noise_decay_bands.geojson path above."
            return
        city_name, project_dir = self._get_project_dir()
        self._analysis_status.text = "Building noise shells from pre-computed data..."
        self._run_async(self._visualise_precomputed_noise_async(
            city_name, link_noise_path, decay_bands_path,
            os.path.join(project_dir, "Noise")))

    async def _visualise_precomputed_noise_async(self, city_name, link_noise_path,
                                                  decay_bands_path, output_dir):
        loop = asyncio.get_event_loop()
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        opacity  = self._shell_opacity_slider.model.get_value_as_float()
        stage    = omni.usd.get_context().get_stage()
        ok, msg  = await apply_noise_shell_from_precomputed(
            decay_bands_path, link_noise_path, lat, lon, output_dir, stage, opacity=opacity)
        self._analysis_status.text = msg if ok else f"Failed: {msg}"
        if ok:
            self._show_noise_legend(msg)

    def _on_shell_opacity_changed(self, model):
        stage = omni.usd.get_context().get_stage()
        if stage:
            update_shell_opacity(stage, model.get_value_as_float())

    def _visualise_cartographic_noise(self):
        link_noise_path  = self._link_noise_input.model.get_value_as_string().strip()
        decay_bands_path = self._decay_bands_input.model.get_value_as_string().strip()
        if not link_noise_path:
            self._analysis_status.text = "Set link_noise.geojson path above."
            return
        if not decay_bands_path:
            self._analysis_status.text = "Set noise_decay_bands.geojson path above."
            return
        city_name, _ = self._get_project_dir()
        self._analysis_status.text = "Building cartographic noise drape..."
        self._run_async(self._visualise_cartographic_noise_async(
            city_name, link_noise_path, decay_bands_path))

    async def _visualise_cartographic_noise_async(self, city_name, link_noise_path, decay_bands_path):
        loop = asyncio.get_event_loop()
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        stage = omni.usd.get_context().get_stage()
        ok, msg = await apply_noise_cartographic(link_noise_path, decay_bands_path, lat, lon, stage)
        self._analysis_status.text = msg if ok else f"Failed: {msg}"
        if ok:
            self._show_cartographic_legend(msg)

    # ── Raster heatmap ────────────────────────────────────────────────────────

    def _create_noise_raster(self):
        if not _RASTER_OK:
            self._analysis_status.text = "GeoTIFF raster module not available"
            return
        link_noise_path = self._link_noise_input.model.get_value_as_string().strip()
        if not link_noise_path:
            self._analysis_status.text = "Set link_noise.geojson path above."
            return
        city_name, project_dir = self._get_project_dir()
        resolution_m   = self._raster_res_input.model.get_value_as_float()
        output_geotiff = os.path.join(project_dir, "Noise", f"noise_heatmap_{resolution_m:.0f}m.tif")
        self._analysis_status.text = f"Creating raster heatmap ({resolution_m:.0f}m)..."
        self._run_async(self._create_noise_raster_async(
            city_name, link_noise_path, output_geotiff, resolution_m))

    async def _create_noise_raster_async(self, city_name, link_noise_path, output_geotiff, resolution_m):
        loop = asyncio.get_event_loop()
        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        ok, msg, tiff_path = await create_noise_raster_geotiff(
            link_noise_path, output_geotiff, lat, lon,
            resolution_m=resolution_m, interpolation_method="linear")
        if not ok:
            self._analysis_status.text = f"Raster creation failed: {msg}"
            return
        self._analysis_status.text = f"{msg} - applying overlay..."
        stage = omni.usd.get_context().get_stage()
        ok2, msg2 = await apply_noise_raster_overlay(tiff_path, lat, lon, stage)
        self._analysis_status.text = msg2 if ok2 else f"Overlay failed: {msg2}"
        if ok2:
            self._show_noise_legend(f"Raster heatmap {resolution_m:.0f}m")

    # ── Noise band click-to-inspect ───────────────────────────────────────────

    def _noise_on_stage_event(self, event):
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        try:
            selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        except Exception:
            return
        for path in selected:
            parts     = path.split("/")
            prim_name = parts[-1]
            if len(parts) < 4:
                continue
            if parts[2] not in ("NoiseDecay", "NoiseCartographic"):
                continue
            if not (prim_name.startswith("Shell_") or prim_name.startswith("Band_")):
                continue
            try:
                stage = omni.usd.get_context().get_stage()
                prim  = stage.GetPrimAtPath(path)
                if not prim.IsValid():
                    continue
                def _get(name):
                    a = prim.GetAttribute(name)
                    return a.Get() if a.IsValid() else None
                noise_db  = _get("custom:noise_db")
                dist_band = _get("custom:distance_band")
                source_db = _get("custom:source_db")
                road_idx  = _get("custom:road_idx")
                if noise_db is not None:
                    self._noise_show_info_panel(
                        path, float(noise_db), str(dist_band or ""), road_idx, source_db)
                    return
            except Exception as exc:
                carb.log_warn(f"[noise_viz] selection read error: {exc}")
            return

    def _noise_show_info_panel(self, prim_path, noise_db, distance_band, road_idx, source_db):
        if getattr(self, "_noise_info_window", None):
            try:
                self._noise_info_window.destroy()
            except Exception:
                pass
            self._noise_info_window = None
        self._noise_info_window = _ui_components.build_noise_info_panel(
            prim_path, noise_db, distance_band, road_idx, source_db)