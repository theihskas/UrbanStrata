"""
mixin_planning.py — Traffic planning, transit, accessibility and metrics callbacks.
"""
import os
import asyncio
from argparse import Namespace
import carb
import omni.usd
from pxr import UsdGeom

import my_rapidex.my_python_ui_extension.metrics as _metrics
import my_rapidex.my_python_ui_extension.modeling_ops as _modeling_ops


class PlanningMixin:

    # ── PCU estimation ────────────────────────────────────────────────────────

    def _run_pcu_estimation(self):
        if not self._rapidxpy_available:
            return self._set_planning_status("RapidXPy not available")
        city_name, city_dir = self._get_project_dir()
        if not city_name or not os.path.exists(city_dir):
            return self._set_planning_status("City directory not found")

        algo_names = ["MSA", "FW", "CFW", "BFW", "TAPAS"]
        sel_names  = ["tournament", "roulette", "random"]
        cx_names   = ["smart", "uniform", "singlepoint"]
        mut_names  = ["swap", "inversion", "scramble", "random"]
        fit_names  = ["MODT", "RODT", "MLT", "RLT", "MLF", "RLF", "RelVarOD"]

        def idx(w):
            return w.model.get_item_value_model().get_value_as_int()

        pcu_args = Namespace(
            estimatetype="PCU",
            algorithm=algo_names[idx(self._est_algorithm_selector)],
            maxIterations=self._est_max_iter_input.model.get_value_as_int(),
            relGap=self._est_rel_gap_input.model.get_value_as_float(),
            capA=[0.6, 0.61, 0.1], capF=[0.7, 0.71, 0.1], ffTF=[1, 1.01, 0.1],
            Generations=self._est_generations_input.model.get_value_as_int(),
            Population=self._est_population_input.model.get_value_as_int(),
            stopThreshold=self._est_stop_threshold_input.model.get_value_as_float(),
            selectiontype=sel_names[idx(self._est_selection_selector)],
            crossovertype=cx_names[idx(self._est_crossover_selector)],
            mutationtype=mut_names[idx(self._est_mutation_selector)],
            crossprob=self._est_cross_prob_input.model.get_value_as_float(),
            mutationprob=self._est_mut_prob_input.model.get_value_as_float(),
            mutationvariability=self._est_mut_var_input.model.get_value_as_float(),
            elites=self._est_elites_input.model.get_value_as_float(),
            minDemand=self._est_min_demand_input.model.get_value_as_float(),
            maxDemand=self._est_max_demand_input.model.get_value_as_float(),
            tolerance=0.0,
            fitnessmethod=fit_names[idx(self._est_fitness_selector)],
            initalsol=[25, 25, 25, 25, 0],
            randomSeed=self._est_seed_input.model.get_value_as_int(),
            cpu=self._est_cpu_input.model.get_value_as_int(),
            zoneFolder=None, linkTTfile=None, elitefolder=None,
            y=True, verbose=False, refine=False,
            learningrate=0.01, maxiters=1000, rtolerance=0.001, momentum=0.9,
            visualize=False,
        )
        self._set_planning_status("PCU estimation running...")
        self._run_async(self._run_pcu_async(city_name, city_dir, pcu_args))

    async def _run_pcu_async(self, city_name, city_dir, pcu_args):
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: _modeling_ops.run_pcu_estimation(city_dir, pcu_args))
        self._set_planning_status(msg)

    # ── Traffic assignment ────────────────────────────────────────────────────

    def _run_msa_assignment(self):
        if not self._rapidxpy_available:
            return self._set_planning_status("RapidXPy not available")
        city_name, project_dir = self._get_project_dir()
        if not city_name or not os.path.exists(project_dir):
            return self._set_planning_status("Project directory not found")

        algo_names = ["MSA", "FW", "CFW", "BFW", "TAPAS"]
        def idx(w):
            return w.model.get_item_value_model().get_value_as_int()

        algorithm = algo_names[idx(self._assign_algorithm_selector)]
        cap_a = self._assign_cap_a_input.model.get_value_as_float()
        cap_f = self._assign_cap_f_input.model.get_value_as_float()
        dem_f = self._assign_dem_f_input.model.get_value_as_float()

        assign_args = Namespace(
            algorithm=algorithm,
            relGap=self._assign_rel_gap_input.model.get_value_as_float(),
            maxIterations=self._assign_max_iter_input.model.get_value_as_int(),
            capA=[cap_a, cap_a + 0.01, 0.1],
            capF=[cap_f, cap_f + 0.01, 0.1],
            ffTF=[1, 1.01, 0.1],
            demF=[dem_f, dem_f + 0.01, 0.1],
            zoneFolder=None, odDemandsfile=None,
            cpu=max(1, (os.cpu_count() or 4) - 2),
            y=True, verbose=False,
            proportions=self._assign_proportions_cb.model.get_value_as_bool(),
            otpdir=None, otpdeparturedate=None, otpdeparturetime=None,
        )
        self._set_planning_status(f"Running {algorithm} assignment...")
        self._run_async(self._run_assign_async(project_dir, assign_args))

    async def _run_assign_async(self, project_dir, assign_args):
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: _modeling_ops.run_assignment(project_dir, assign_args))
        self._set_planning_status(msg)

    def _run_full_planning(self):
        self._set_planning_status("Running full plan (PCU + MSA) in background...")
        self._run_pcu_estimation()
        self._set_planning_status("Full plan: PCU + MSA queued")

    # ── Transit / OTP ─────────────────────────────────────────────────────────

    def _build_otp_graph(self):
        otp_dir = self._otp_dir_input.model.get_value_as_string()
        self._transit_status.text = "Building OTP graph..."
        ok, msg = _modeling_ops.build_otp_graph(otp_dir)
        self._transit_status.text = msg

    def _start_otp_server(self):
        otp_dir = self._otp_dir_input.model.get_value_as_string()
        ok, msg, proc = _modeling_ops.start_otp_server(otp_dir)
        if ok:
            self._otp_process = proc
        self._transit_status.text = msg

    def _stop_otp_server(self):
        if self._otp_process:
            ok, msg = _modeling_ops.stop_otp_server(self._otp_process)
            self._otp_process = None
            self._transit_status.text = msg

    def _run_transit_zone_analysis(self):
        from datetime import datetime
        import argparse
        city_name, project_dir = self._get_project_dir()
        otp_dir   = self._otp_dir_input.model.get_value_as_string()
        dep_str   = self._otp_departure_input.model.get_value_as_string()
        dep_dt    = datetime.strptime(dep_str, "%Y-%m-%d %H:%M")
        zones_dir = os.path.join(project_dir, "Zones_H3")
        if not os.path.isdir(zones_dir):
            zones_dir = os.path.join(project_dir, "Zones_default")
        args = argparse.Namespace(
            otp_dir=otp_dir, zones_dir=zones_dir,
            departure_date=dep_dt.strftime("%Y-%m-%d"),
            departure_time=dep_dt.strftime("%H:%M:%S"),
            max_walk_distance=self._otp_max_walk_input.model.get_value_as_int(),
            num_itineraries=1,
            workers=self._otp_workers_input.model.get_value_as_int(),
            output=os.path.join(project_dir, "transit_zone_results.json"),
            gql_timeout=self._otp_gql_timeout_input.model.get_value_as_int(),
        )
        self._transit_status.text = "Running zone-to-zone transit analysis..."
        ok, msg = _modeling_ops.run_transit_zone_analysis(args)
        self._transit_status.text = msg

    def _run_transit_point_analysis(self):
        from datetime import datetime
        import argparse
        otp_dir = self._otp_dir_input.model.get_value_as_string()
        dep_str = self._otp_departure_input.model.get_value_as_string()
        dep_dt  = datetime.strptime(dep_str, "%Y-%m-%d %H:%M")
        args = argparse.Namespace(
            otp_dir=otp_dir,
            origin=self._otp_origin_input.model.get_value_as_string(),
            destination=self._otp_dest_input.model.get_value_as_string(),
            departure_date=dep_dt.strftime("%Y-%m-%d"),
            departure_time=dep_dt.strftime("%H:%M:%S"),
            num_itineraries=self._otp_num_itin_input.model.get_value_as_int(),
            gql_timeout=self._otp_gql_timeout_input.model.get_value_as_int(),
        )
        self._transit_status.text = "Running point-to-point transit route..."
        ok, msg = _modeling_ops.run_transit_point_analysis(args)
        self._transit_status.text = msg

    # ── Advanced planning ─────────────────────────────────────────────────────

    def _run_decompose(self):
        import argparse
        _, project_dir = self._get_project_dir()
        if not os.path.isdir(project_dir):
            self._advanced_status.text = f"Project dir not found: {project_dir}"
            return
        args = argparse.Namespace(zoneFolder=None, results_folder=None, y=True, verbose=False)
        ok, msg = _modeling_ops.run_decompose(project_dir, args)
        self._advanced_status.text = msg

    def _run_refine(self):
        import argparse
        _, project_dir = self._get_project_dir()
        args = argparse.Namespace(
            zoneFolder=None, linkTTfile=None,
            learningrate=self._refine_lr_input.model.get_value_as_float(),
            maxiters=self._refine_iters_input.model.get_value_as_int(),
            rtolerance=0.001, momentum=0.9, y=True, verbose=False,
        )
        ok, msg = _modeling_ops.run_decompose(project_dir, args)
        self._advanced_status.text = msg

    def _run_robustness(self):
        _, project_dir = self._get_project_dir()
        ok, msg = _modeling_ops.run_robustness(project_dir)
        self._advanced_status.text = msg

    # ── Multi-modal ───────────────────────────────────────────────────────────

    def _run_gtfs_metrics(self):
        _, project_dir = self._get_project_dir()
        gtfs_folder   = self._gtfs_dir_input.model.get_value_as_string()
        zones_geojson = self._find_zones_geojson(project_dir)
        self._multimodal_status.text = "Computing GTFS zone metrics..."
        ok, msg = _modeling_ops.run_gtfs_metrics(gtfs_folder, zones_geojson)
        self._multimodal_status.text = msg

    def _run_mode_split(self):
        _, project_dir = self._get_project_dir()
        gtfs_folder   = self._gtfs_dir_input.model.get_value_as_string()
        zones_geojson = self._find_zones_geojson(project_dir)
        self._multimodal_status.text = "Running mode split..."
        ok, msg = _modeling_ops.run_mode_split(project_dir, gtfs_folder, zones_geojson)
        self._multimodal_status.text = msg

    # ── Accessibility ─────────────────────────────────────────────────────────

    def _run_accessibility(self):
        poi_sources = ["osm", "google", "safegraph"]
        poi_src = poi_sources[self._poi_selector.model.get_item_value_model().get_value_as_int()]
        _, project_dir = self._get_project_dir()
        zones = self._find_zones_geojson(project_dir)
        centroids = zones.replace("Zones.geojson", "Centroids.geojson")
        self._accessibility_status.text = f"Running accessibility ({poi_src})..."
        ok, msg = _modeling_ops.run_accessibility(project_dir, zones, centroids, poi_src)
        self._accessibility_status.text = msg

    # ── Metrics viewports ─────────────────────────────────────────────────────

    def _create_equity_viewport(self):
        _, project_dir = self._get_project_dir()
        stage  = omni.usd.get_context().get_stage()
        flows  = _metrics.get_assignment_flows(project_dir)
        values = _metrics.compute_equity_values(flows)
        _metrics.create_metric_overlay(stage, "equity", values, self._link_width)
        _metrics.ensure_viewport("Equity Metrics", 100, 100)
        _metrics.set_overlay_visibility(stage, "equity")
        self._metrics_status.text = f"Equity: {len(values)} edges colourised"

    def _create_accessibility_viewport(self):
        _, project_dir = self._get_project_dir()
        stage  = omni.usd.get_context().get_stage()
        flows  = _metrics.get_assignment_flows(project_dir)
        values = _metrics.compute_accessibility_values(flows)
        _metrics.create_metric_overlay(stage, "accessibility", values, self._link_width)
        _metrics.ensure_viewport("Accessibility Metrics", 760, 100)
        _metrics.set_overlay_visibility(stage, "accessibility")
        self._metrics_status.text = f"Accessibility: {len(values)} edges colourised"

    def _create_carbon_viewport(self):
        _, project_dir = self._get_project_dir()
        stage  = omni.usd.get_context().get_stage()
        flows  = _metrics.get_assignment_flows(project_dir)
        factor = self._carbon_factor_input.model.get_value_as_float()
        values = _metrics.compute_carbon_values(flows, stage, factor)
        _metrics.create_metric_overlay(stage, "carbon", values, self._link_width)
        _metrics.ensure_viewport("Carbon Emissions", 100, 620)
        _metrics.set_overlay_visibility(stage, "carbon")
        self._metrics_status.text = f"Carbon: {len(values)} edges colourised"

    def _compute_and_apply_metrics(self):
        self._metrics_status.text = "Computing all metrics..."
        _, project_dir = self._get_project_dir()
        stage  = omni.usd.get_context().get_stage()
        flows  = _metrics.get_assignment_flows(project_dir)
        if not flows:
            self._metrics_status.text = "No assignment results — run assignment first"
            return
        factor = self._carbon_factor_input.model.get_value_as_float()
        _metrics.create_metric_overlay(stage, "equity",
                                       _metrics.compute_equity_values(flows), self._link_width)
        _metrics.create_metric_overlay(stage, "accessibility",
                                       _metrics.compute_accessibility_values(flows), self._link_width)
        _metrics.create_metric_overlay(stage, "carbon",
                                       _metrics.compute_carbon_values(flows, stage, factor), self._link_width)
        _metrics.ensure_viewport("Equity Metrics",        100, 100)
        _metrics.ensure_viewport("Accessibility Metrics", 760, 100)
        _metrics.ensure_viewport("Carbon Emissions",      100, 620)
        for m in _metrics.METRICS:
            prim = stage.GetPrimAtPath(f"/World/MetricOverlay/{m.capitalize()}")
            if prim.IsValid():
                UsdGeom.Imageable(prim).MakeVisible()
        self._metrics_status.text = f"All metrics: {len(flows)} edges | 3 viewports"

    def _create_zone_metric_layers(self):
        from my_rapidex.my_python_ui_extension.network_ops import get_city_coordinates
        city_name, project_dir = self._get_project_dir()
        zones_path = self._find_zones_geojson(project_dir)
        if not os.path.exists(zones_path):
            self._analysis_status.text = "No Zones.geojson found — apply zoning first"
            return
        stage  = omni.usd.get_context().get_stage()
        flows  = _metrics.get_assignment_flows(project_dir)
        lat, lon = get_city_coordinates(city_name)
        factor = self._carbon_factor_input.model.get_value_as_float()
        try:
            count = _metrics.create_zone_layers(zones_path, project_dir, stage, flows, factor, lat, lon)
            self._analysis_status.text = f"Zone layers: {count} zones (emissions + accessibility)"
        except Exception as exc:
            self._analysis_status.text = f"Zone layers failed: {exc}"
            carb.log_error(f"[analysis] zone layers: {exc}")