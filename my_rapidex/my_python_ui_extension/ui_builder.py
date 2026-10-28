"""
ui_builder.py
=============
Constructs the main extension window and its layout.
Separated from extension.py to keep the main class lightweight.
"""
import os
from datetime import datetime
import omni.ui as ui

def build_main_window(ext, rapidx_available, core_available):
    S = {"background_color": 0xFF3D4450}
    F = {"CollapsableFrame": {"background_color": 0xFF2A2A2A}}

    def row(label, widget_fn, label_width=140):
        with ui.HStack(height=22, spacing=4):
            ui.Label(label, width=label_width)
            return widget_fn()

    ext._window = ui.Window(
        "RapidXPy Traffic Digital Twin",
        width=500, height=900,
        flags=(ui.WINDOW_FLAGS_NO_COLLAPSE |
               ui.WINDOW_FLAGS_NO_CLOSE),
    )

    with ext._window.frame:
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
        ):
            with ui.VStack(spacing=8):
                ui.Label("RapidXPy Traffic Digital Twin", height=35,
                         style={"font_size": 20, "color": 0xFFCC8855})
                ui.Separator()

                # ── City Operations ───────────────────────────────────────
                with ui.CollapsableFrame("City Operations", collapsed=False, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("City name / place:")
                        ext._city_input = ui.StringField()
                        ext._city_input.model.set_value(ext._city_name)
                        ui.Label("Output base path:")
                        ext._base_path_input = ui.StringField()
                        ext._base_path_input.model.set_value(ext._base_path)

                        with ui.CollapsableFrame("Network Parameters", collapsed=False, style=F):
                            with ui.VStack(spacing=4):
                                ui.Label("Road types (comma-separated):",
                                         style={"color": 0xFF999999})
                                ext._road_types_input = ui.StringField()
                                ext._road_types_input.model.set_value(
                                    "motorway,trunk,primary,secondary,tertiary")
                                ui.Label("Default speeds km/h (one per road type):",
                                         style={"color": 0xFF999999})
                                ext._default_speed_input = ui.StringField()
                                ext._default_speed_input.model.set_value("120,100,80,60,40")
                                ext._zone_type_selector = row(
                                    "Zone type:", lambda: ui.ComboBox(0, "H3", "Square"))
                                ext._zone_resolution_input = row(
                                    "H3 resolution (1-15):", lambda: ui.IntField(height=22))
                                ext._zone_resolution_input.model.set_value(7)
                                ext._zone_levels_input = row(
                                    "Zone levels:", lambda: ui.IntField(height=22))
                                ext._zone_levels_input.model.set_value(2)
                                ui.Label("Custom zone file (optional):",
                                         style={"color": 0xFF999999})
                                ext._zone_file_input = ui.StringField()
                                ext._zone_file_input.model.set_value("")
                                ui.Label("POI API for zoning:",
                                         style={"color": 0xFF999999})
                                ext._poi_api_input = ui.StringField()
                                ext._poi_api_input.model.set_value("safegraph")

                        with ui.HStack(spacing=4):
                            ui.Button("Download Network", clicked_fn=ext._download_network,
                                      height=30, style=S)
                            ui.Button("Build Network",    clicked_fn=ext._build_network,
                                      height=30, style=S)
                        with ui.HStack(spacing=4):
                            ui.Button("Apply Zoning",  clicked_fn=ext._apply_zoning,
                                      height=30, style=S)
                            ui.Button("Load Network",  clicked_fn=ext._load_network,
                                      height=30, style=S)

                        ui.Separator()
                        ui.Label("OSM Buildings (selectable prims):",
                                 style={"font_size": 13, "color": 0xFFCCAA55})
                        ui.Label("Max buildings (0 for all, high values may crash):",
                                 style={"color": 0xFFBB6633})
                        ext._buildings_max_input = ui.IntField()
                        ext._buildings_max_input.model.set_value(50000)
                        ext._buildings_max_model = ext._buildings_max_input.model
                        ui.Label("Noise GeoJSON (optional, for noise_leq join):",
                                 style={"color": 0xFF999999})
                        ext._buildings_noise_input = ui.StringField()
                        ext._buildings_noise_input.model.set_value("")
                        ui.Button("Load OSM Buildings",
                                  clicked_fn=ext._load_osm_buildings,
                                  height=32, style=S)

                        ext._network_status = ui.Label(
                            "Network: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Travel Time Operations ────────────────────────────────
                with ui.CollapsableFrame("Travel Time Operations", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ext._api_selector = row(
                            "API:", lambda: ui.ComboBox(0, "TomTom", "Google", "Mapbox"))
                        ui.Label("API key (TomTom / Google / Mapbox):",
                                 style={"color": 0xFF999999})
                        ext._travel_api_key_input = ui.StringField()
                        ext._travel_api_key_input.model.set_value("")
                        with ui.CollapsableFrame("Travel Time Parameters",
                                                 collapsed=True, style=F):
                            with ui.VStack(spacing=4):
                                ui.Label("Hours (e.g. 8,17  or  now  or  all):",
                                         style={"color": 0xFF999999})
                                ext._tt_hours_input = ui.StringField()
                                ext._tt_hours_input.model.set_value("now")
                                ext._tt_date_range_input = row(
                                    "Date range (days):", lambda: ui.IntField(height=22))
                                ext._tt_date_range_input.model.set_value(1)
                                ext._tt_min_length_input = row(
                                    "Min link length (m):", lambda: ui.FloatField(height=22))
                                ext._tt_min_length_input.model.set_value(50.0)
                        ui.Button("Fetch Travel Times", clicked_fn=ext._fetch_travel_times,
                                  height=35, style=S)
                        ext._travel_time_status = ui.Label(
                            "Travel Times: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Traffic Planning ──────────────────────────────────────
                with ui.CollapsableFrame("Traffic Planning", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        with ui.CollapsableFrame("Estimation Parameters (GA)",
                                                 collapsed=True, style=F):
                            with ui.VStack(spacing=4):
                                ext._est_algorithm_selector = row(
                                    "Assignment algo:",
                                    lambda: ui.ComboBox(0, "MSA", "FW", "CFW", "BFW", "TAPAS"))
                                ext._est_max_iter_input = row(
                                    "Max iterations:", lambda: ui.IntField(height=22))
                                ext._est_max_iter_input.model.set_value(1000)
                                ext._est_rel_gap_input = row(
                                    "Rel gap:", lambda: ui.FloatField(height=22))
                                ext._est_rel_gap_input.model.set_value(0.01)
                                ext._est_generations_input = row(
                                    "GA generations:", lambda: ui.IntField(height=22))
                                ext._est_generations_input.model.set_value(200)
                                ext._est_population_input = row(
                                    "GA population:", lambda: ui.IntField(height=22))
                                ext._est_population_input.model.set_value(100)
                                ext._est_stop_threshold_input = row(
                                    "Stop threshold:", lambda: ui.FloatField(height=22))
                                ext._est_stop_threshold_input.model.set_value(10.0)
                                ext._est_selection_selector = row(
                                    "Selection:",
                                    lambda: ui.ComboBox(0, "tournament", "roulette", "random"))
                                ext._est_crossover_selector = row(
                                    "Crossover:",
                                    lambda: ui.ComboBox(0, "smart", "uniform", "singlepoint"))
                                ext._est_mutation_selector = row(
                                    "Mutation:",
                                    lambda: ui.ComboBox(0, "swap", "inversion",
                                                         "scramble", "random"))
                                ext._est_cross_prob_input = row(
                                    "Crossover prob:", lambda: ui.FloatField(height=22))
                                ext._est_cross_prob_input.model.set_value(0.8)
                                ext._est_mut_prob_input = row(
                                    "Mutation prob:", lambda: ui.FloatField(height=22))
                                ext._est_mut_prob_input.model.set_value(0.2)
                                ext._est_mut_var_input = row(
                                    "Mut variability:", lambda: ui.FloatField(height=22))
                                ext._est_mut_var_input.model.set_value(0.8)
                                ext._est_fitness_selector = row(
                                    "Fitness method:",
                                    lambda: ui.ComboBox(0, "MODT", "RODT", "MLT",
                                                         "RLT", "MLF", "RLF", "RelVarOD"))
                                ext._est_min_demand_input = row(
                                    "Min demand:", lambda: ui.FloatField(height=22))
                                ext._est_min_demand_input.model.set_value(5000.0)
                                ext._est_max_demand_input = row(
                                    "Max demand:", lambda: ui.FloatField(height=22))
                                ext._est_max_demand_input.model.set_value(300000.0)
                                ext._est_elites_input = row(
                                    "Elite fraction:", lambda: ui.FloatField(height=22))
                                ext._est_elites_input.model.set_value(0.1)
                                ext._est_seed_input = row(
                                    "Random seed:", lambda: ui.IntField(height=22))
                                ext._est_seed_input.model.set_value(4)
                                ext._est_cpu_input = row(
                                    "CPU cores:", lambda: ui.IntField(height=22))
                                ext._est_cpu_input.model.set_value(
                                    max(1, (os.cpu_count() or 4) - 2))

                        with ui.CollapsableFrame("Assignment Parameters",
                                                 collapsed=True, style=F):
                            with ui.VStack(spacing=4):
                                ext._assign_algorithm_selector = row(
                                    "Algorithm:",
                                    lambda: ui.ComboBox(0, "MSA", "FW", "CFW", "BFW", "TAPAS"))
                                ext._assign_max_iter_input = row(
                                    "Max iterations:", lambda: ui.IntField(height=22))
                                ext._assign_max_iter_input.model.set_value(1000)
                                ext._assign_rel_gap_input = row(
                                    "Rel gap:", lambda: ui.FloatField(height=22))
                                ext._assign_rel_gap_input.model.set_value(0.0001)
                                ext._assign_cap_a_input = row(
                                    "Cap arterial (min):", lambda: ui.FloatField(height=22))
                                ext._assign_cap_a_input.model.set_value(0.6)
                                ext._assign_cap_f_input = row(
                                    "Cap freeway (min):", lambda: ui.FloatField(height=22))
                                ext._assign_cap_f_input.model.set_value(0.7)
                                ext._assign_dem_f_input = row(
                                    "Demand factor (min):", lambda: ui.FloatField(height=22))
                                ext._assign_dem_f_input.model.set_value(1.0)
                                with ui.HStack(height=22, spacing=4):
                                    ui.Label("Output OD proportions:", width=140)
                                    ext._assign_proportions_cb = ui.CheckBox(width=22)
                                    ext._assign_proportions_cb.model.set_value(False)

                        with ui.HStack(spacing=4):
                            ui.Button("PCU Estimate", clicked_fn=ext._run_pcu_estimation,
                                      height=30, style=S)
                            ui.Button("MSA Assign",   clicked_fn=ext._run_msa_assignment,
                                      height=30, style=S)
                        ui.Button("Full Plan (PCU + Assign)",
                                  clicked_fn=ext._run_full_planning, height=30, style=S)
                        ext._planning_status = ui.Label(
                            "Planning: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Network Fix ───────────────────────────────────────────
                with ui.CollapsableFrame("Network Fix", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("Update link speeds from fetched travel times:",
                                 style={"color": 0xFF999999})
                        ui.Button("Fix Network Speeds", clicked_fn=ext._fix_network,
                                  height=30, style=S)
                        ext._fix_status = ui.Label(
                            "Fix: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Transit Analysis (OTP) ────────────────────────────────
                with ui.CollapsableFrame("Transit Analysis (OTP)", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("OTP directory:")
                        ext._otp_dir_input = ui.StringField()
                        ext._otp_dir_input.model.set_value("D:/otp")
                        with ui.HStack(spacing=4):
                            ui.Button("Build Graph",  clicked_fn=ext._build_otp_graph,
                                      height=30, style=S)
                            ui.Button("Start Server", clicked_fn=ext._start_otp_server,
                                      height=30, style=S)
                            ui.Button("Stop Server",  clicked_fn=ext._stop_otp_server,
                                      height=30, style=S)
                        with ui.CollapsableFrame("Transit Parameters",
                                                 collapsed=True, style=F):
                            with ui.VStack(spacing=4):
                                ext._otp_max_walk_input = row(
                                    "Max walk dist (m):", lambda: ui.IntField(height=22))
                                ext._otp_max_walk_input.model.set_value(1000)
                                ext._otp_num_itin_input = row(
                                    "Itineraries:", lambda: ui.IntField(height=22))
                                ext._otp_num_itin_input.model.set_value(3)
                                ext._otp_workers_input = row(
                                    "Parallel workers:", lambda: ui.IntField(height=22))
                                ext._otp_workers_input.model.set_value(4)
                                ext._otp_gql_timeout_input = row(
                                    "GQL timeout (s):", lambda: ui.IntField(height=22))
                                ext._otp_gql_timeout_input.model.set_value(30)
                        ui.Label("Departure (YYYY-MM-DD HH:MM):")
                        ext._otp_departure_input = ui.StringField()
                        ext._otp_departure_input.model.set_value(
                            datetime.now().strftime("%Y-%m-%d %H:%M"))
                        ui.Button("Zone-to-Zone Transit Analysis",
                                  clicked_fn=ext._run_transit_zone_analysis,
                                  height=30, style=S)
                        ui.Label("Origin (lat,lon):")
                        ext._otp_origin_input = ui.StringField()
                        ext._otp_origin_input.model.set_value("51.0504,13.7373")
                        ui.Label("Destination (lat,lon):")
                        ext._otp_dest_input = ui.StringField()
                        ext._otp_dest_input.model.set_value("51.0600,13.7500")
                        ui.Button("Point-to-Point Transit Route",
                                  clicked_fn=ext._run_transit_point_analysis,
                                  height=30, style=S)
                        ext._transit_status = ui.Label(
                            "Transit: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Advanced Planning ─────────────────────────────────────
                with ui.CollapsableFrame("Advanced Planning", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Button("Decompose OD Demand",
                                  clicked_fn=ext._run_decompose, height=30, style=S)
                        ui.Separator()
                        ui.Label("Demand Refinement:", style={"color": 0xFF999999})
                        ext._refine_lr_input = row(
                            "Learning rate:", lambda: ui.FloatField(height=22))
                        ext._refine_lr_input.model.set_value(0.01)
                        ext._refine_iters_input = row(
                            "Max iterations:", lambda: ui.IntField(height=22))
                        ext._refine_iters_input.model.set_value(1000)
                        ui.Button("Refine Demand", clicked_fn=ext._run_refine,
                                  height=30, style=S)
                        ui.Separator()
                        ui.Button("Network Robustness Analysis",
                                  clicked_fn=ext._run_robustness, height=30, style=S)
                        ext._advanced_status = ui.Label(
                            "Advanced: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Multi-Modal Analysis ──────────────────────────────────
                with ui.CollapsableFrame("Multi-Modal Analysis", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("GTFS folder:")
                        ext._gtfs_dir_input = ui.StringField()
                        ext._gtfs_dir_input.model.set_value("D:/gtfs")
                        ui.Button("Compute GTFS Zone Metrics",
                                  clicked_fn=ext._run_gtfs_metrics, height=30, style=S)
                        ui.Button("Run Mode Split",
                                  clicked_fn=ext._run_mode_split, height=30, style=S)
                        ext._multimodal_status = ui.Label(
                            "Multi-Modal: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Accessibility Analysis ────────────────────────────────
                with ui.CollapsableFrame("Accessibility Analysis", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ext._poi_selector = row(
                            "POI source:",
                            lambda: ui.ComboBox(0, "OpenStreetMap",
                                                 "Google Places", "SafeGraph"))
                        ui.Button("Run Accessibility Analysis",
                                  clicked_fn=ext._run_accessibility, height=35, style=S)
                        ext._accessibility_status = ui.Label(
                            "Accessibility: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Metrics Viewports ─────────────────────────────────────
                with ui.CollapsableFrame("Metrics Viewports", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("Create separate viewports for each metric:",
                                 style={"color": 0xFF999999})
                        with ui.HStack(spacing=4):
                            ui.Button("Equity",        clicked_fn=ext._create_equity_viewport,
                                      height=30, style=S)
                            ui.Button("Accessibility",
                                      clicked_fn=ext._create_accessibility_viewport,
                                      height=30, style=S)
                            ui.Button("Carbon",        clicked_fn=ext._create_carbon_viewport,
                                      height=30, style=S)
                        ext._carbon_factor_input = row(
                            "CO₂ g/km/veh:", lambda: ui.FloatField(height=22))
                        ext._carbon_factor_input.model.set_value(120.0)
                        ext._access_threshold_input = row(
                            "Access threshold (min):", lambda: ui.IntField(height=22))
                        ext._access_threshold_input.model.set_value(30)
                        ui.Button("Compute & Apply All Metrics",
                                  clicked_fn=ext._compute_and_apply_metrics,
                                  height=30, style=S)
                        ext._metrics_status = ui.Label(
                            "Metrics: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Traffic Visualization ─────────────────────────────────
                with ui.CollapsableFrame("Traffic Visualization", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("Estimate results folder (optional, or direct .csv path):",
                                 style={"color": 0xFF999999})
                        ext._viz_results_folder_input = ui.StringField()
                        ext._viz_results_folder_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup"
                            r"\Zones_default_HEX\Results\2025-08-11_13-10-54"
                            r"\Estimation\CapA-0.60_CapF-0.70_FFTT-1.00\Elite0LinkResults.csv"
                        )
                        ui.Label("Time sub-folder (e.g. Hour_08, leave blank to auto-find):",
                                 style={"color": 0xFF999999})
                        ext._viz_time_folder_input = ui.StringField()
                        ext._viz_time_folder_input.model.set_value("")
                        with ui.HStack(spacing=4):
                            ui.Button("Animate Traffic Flow",
                                      clicked_fn=ext._animate_traffic_flow,
                                      height=30, style=S)
                            ui.Button("Add Volume Labels",
                                      clicked_fn=ext._add_volume_labels,
                                      height=30, style=S)
                        with ui.HStack(spacing=4):
                            ui.Button("Style Nodes",
                                      clicked_fn=ext._style_nodes,
                                      height=30, style=S)
                            ui.Button("Style Centroids",
                                      clicked_fn=ext._style_centroids,
                                      height=30, style=S)
                        ext._traffic_viz_status = ui.Label(
                            "Viz: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── Network Design Module ─────────────────────────────────
                with ui.CollapsableFrame("Network Design Module", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label(
                            "Click roads in the viewport to edit capacity, "
                            "rerun assignment, then compare before vs after.",
                            style={"color": 0xFF999999, "font_size": 11},
                            word_wrap=True, height=32)
                        ui.Label("Zone Data Folder (with Results/):",
                                 style={"color": 0xFF999999})
                        ext._nd_zone_folder_input = ui.StringField(height=22)
                        ext._nd_zone_folder_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup\Zones_default_HEX"
                        )
                        ui.Label("LinkTT File (LinkTT*.csv):",
                                 style={"color": 0xFF999999})
                        ext._nd_link_tt_input = ui.StringField(height=22)
                        ext._nd_link_tt_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup"
                            r"\Traveltimes\LinkTT_2025-08-11_13-10-54.csv"
                        )

                        with ui.HStack(spacing=4):
                            ext._nd_sel_mode_btn = ui.Button(
                                "Enable Road Selection",
                                clicked_fn=ext._nd_toggle_selection_mode,
                                height=30, style=S)
                            ui.Button(
                                "Clear All Edits",
                                clicked_fn=ext._nd_clear_edits,
                                height=30, style=S)

                        ext._nd_sel_status = ui.Label(
                            "Selection mode: OFF",
                            height=18, style={"color": 0xFF888888})
                        ext._nd_selected_label = ui.Label(
                            "No road selected",
                            height=18, style={"color": 0xFF88AAFF})
                        ext._nd_edit_count_label = ui.Label(
                            "0 road(s) modified (pending)",
                            height=18, style={"color": 0xFFFFAA44})

                        ui.Separator()
                        ui.Label("Modified assignment algorithm:",
                                 style={"color": 0xFF999999})
                        ext._nd_algorithm_selector = row(
                            "Algorithm:",
                            lambda: ui.ComboBox(0, "MSA", "FW", "CFW", "BFW"))
                        ui.Button(
                            "Run Assignment on Modified Network",
                            clicked_fn=ext._nd_run_modified_assignment,
                            height=32, style=S)
                        ext._nd_progress_bar = ui.ProgressBar(
                            height=6,
                            style={"background_color": 0xFF333333, "color": 0xFF44AAFF})
                        ext._nd_progress_bar.model.set_value(0.0)
                        ext._nd_progress_bar.visible = False

                        ui.Separator()
                        ui.Label("Before / After Comparison:",
                                 style={"color": 0xFF999999})
                        with ui.HStack(spacing=4):
                            ui.Button(
                                "Show Original",
                                clicked_fn=ext._nd_show_original_scenario,
                                height=28, style=S)
                            ui.Button(
                                "Show Modified",
                                clicked_fn=ext._nd_show_modified_scenario,
                                height=28, style=S)
                        ui.Button(
                            "Compare Stats (Before vs After)",
                            clicked_fn=ext._nd_compare_before_after,
                            height=30, style=S)

                        ext._nd_status = ui.Label(
                            "Network Design: Ready",
                            height=20, style={"color": 0xFF88CCAA})

                # ── Traffic Analysis (Equity / Noise / Emissions) ─────────
                with ui.CollapsableFrame("Traffic Analysis", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("Tutorial 5-style equity / noise / CO₂ analysis",
                                 style={"color": 0xFF999999})
                        ui.Label("Estimate results folder (shared with Viz above):",
                                 style={"color": 0xFF888888})
                        with ui.HStack(spacing=4):
                            ui.Button("Gini (Accessibility)",
                                      clicked_fn=ext._compute_gini_accessibility,
                                      height=30, style=S)
                            ui.Button("CO₂ Emissions",
                                      clicked_fn=ext._compute_emissions_analysis,
                                      height=30, style=S)
                        with ui.HStack(spacing=4):
                            ui.Button("Noise (Leq)",
                                      clicked_fn=ext._compute_noise_analysis,
                                      height=30, style=S)
                            ui.Button("Create Zone Layers",
                                      clicked_fn=ext._create_zone_metric_layers,
                                      height=30, style=S)
                        ui.Separator()
                        ui.Label("3D Noise Decay (CNOSSOS-EU):",
                                 style={"font_size": 13, "color": 0xFFCCAA55})
                        ui.Label("Elite0LinkResults.csv path:",
                                 style={"color": 0xFF999999})
                        ext._elite_csv_input = ui.StringField()
                        ext._elite_csv_input.model.set_value(
                            "D:/accessibility raw data/dresden_backup/dresden_backup"
                            "/Zones_default_HEX/Results/2025-08-11_13-10-54"
                            "/Estimation/CapA-0.60_CapF-0.70_FFTT-1.00"
                            "/Elite0LinkResults.csv")
                        ui.Label("edges.geojson path (leave blank to auto-find):",
                                 style={"color": 0xFF999999})
                        ext._edges_geojson_input = ui.StringField()
                        ext._edges_geojson_input.model.set_value("")
                        ui.Label("Noise output folder (blank → {project_dir}/Noise):",
                                 style={"color": 0xFF999999})
                        ext._noise_output_folder_input = ui.StringField()
                        ext._noise_output_folder_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup\Noise"
                        )
                        ui.Button("Compute & Visualise 3D Noise Decay",
                                  clicked_fn=ext._compute_noise_decay_3d,
                                  height=32, style=S)
                        ui.Separator()
                        ui.Label("-- OR load pre-computed noise files --",
                                 style={"font_size": 11, "color": 0xFF888888})
                        ui.Label("link_noise.geojson (LineString geometry + noise col):",
                                 style={"color": 0xFF999999})
                        ext._link_noise_input = ui.StringField()
                        ext._link_noise_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup"
                            r"\Noise\link_noise.geojson")
                        ui.Label("noise_decay_bands.geojson (per-link per-band noise_d):",
                                 style={"color": 0xFF999999})
                        ext._decay_bands_input = ui.StringField()
                        ext._decay_bands_input.model.set_value(
                            r"D:\accessibility raw data\dresden_backup\dresden_backup"
                            r"\Noise\noise_decay_bands.geojson")
                        ui.Button("Visualise Pre-computed Noise",
                                  clicked_fn=ext._visualise_precomputed_noise,
                                  height=32, style=S)
                        ui.Separator()
                        with ui.HStack(height=24, spacing=6):
                            ui.Label("Shell opacity:",
                                     width=90, style={"color": 0xFF999999})
                            ext._shell_opacity_slider = ui.FloatSlider(
                                min=0.05, max=1.0, step=0.01)
                            ext._shell_opacity_slider.model.set_value(1.0)
                            ext._shell_opacity_slider.model.add_value_changed_fn(
                                ext._on_shell_opacity_changed)
                        ui.Separator()
                        ui.Label("-- OR 3D cartographic noise (continuous hues) --",
                                 style={"font_size": 11, "color": 0xFF888888})
                        ui.Button("Visualise Noise (3D Cartographic)",
                                  clicked_fn=ext._visualise_cartographic_noise,
                                  height=32, style=S)
                        ui.Separator()
                        ui.Label("-- OR raster heatmap (GeoTIFF texture drape) --",
                                 style={"font_size": 11, "color": 0xFF888888})
                        with ui.HStack(height=24, spacing=6):
                            ui.Label("Raster res (m):",
                                     width=90, style={"color": 0xFF999999})
                            ext._raster_res_input = ui.FloatField()
                            ext._raster_res_input.model.set_value(20.0)
                        ui.Button("Create Noise Raster + Overlay",
                                  clicked_fn=ext._create_noise_raster,
                                  height=32, style=S)
                        ext._analysis_status = ui.Label(
                            "Analysis: Ready", height=20, style={"color": 0xFF88CCAA})

                # ── AI Command Interface ──────────────────────────────────
                with ui.CollapsableFrame("AI Command Interface (natural language → action)",
                                         collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label(
                            "Requires Ollama running locally (ollama.com).",
                            style={"color": 0xFF888888})
                        ui.Label("Model:", style={"color": 0xFF999999})
                        ext._ai_cmd_model_input = ui.StringField()
                        ext._ai_cmd_model_input.model.set_value("llama3.2")
                        ui.Label("Command (plain English):",
                                 style={"color": 0xFF999999})
                        ext._ai_cmd_input = ui.StringField()
                        ext._ai_cmd_input.model.set_value("")
                        ui.Label(
                            "e.g. 'download Munich network'  |  'apply H3 zoning resolution 8'"
                            "  |  'load network into stage'  |  'fetch travel times with mapbox'",
                            style={"color": 0xFF666666})
                        ui.Button("Execute Command", clicked_fn=ext._execute_ai_command,
                                  height=30, style=S)
                        ext._ai_cmd_status = ui.Label(
                            "Ready -type a command above and click Execute",
                            height=60, word_wrap=True,
                            style={"color": 0xFF99DDBB})

                # ── AI Assistant ──────────────────────────────────────────
                with ui.CollapsableFrame("AI Assistant -Conceptual Q&A (Ollama)", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label(
                            "Requires Ollama running locally (ollama.com).",
                            style={"color": 0xFF888888})
                        ui.Label("Model (e.g. llama3.2, mistral, phi3):",
                                 style={"color": 0xFF999999})
                        ext._llm_api_key_input = ui.StringField()
                        ext._llm_api_key_input.model.set_value("llama3.2")
                        ui.Label("Ask about traffic modeling, RapidXPy, or your results:",
                                 style={"color": 0xFF999999})
                        ext._chatbot_input = ui.StringField()
                        ext._chatbot_input.model.set_value("")
                        ui.Button("Ask AI", clicked_fn=ext._send_chatbot_message,
                                  height=30, style=S)
                        ext._chatbot_response = ui.Label(
                            "Response will appear here...",
                            height=100, word_wrap=True,
                            style={"color": 0xFF99BBDD})

                # ── Performance Diagnostics ──────────────────────────────
                with ui.CollapsableFrame("Performance Diagnostics", collapsed=True, style=F):
                    with ui.VStack(spacing=5):
                        ui.Label("Monitor FPS, memory, and GPU health.",
                                 style={"color": 0xFF999999})
                        with ui.HStack(spacing=4):
                            ui.Button("Start 5-min Diagnostic",
                                      clicked_fn=ext._start_fps_monitor,
                                      height=30, style=S)
                            ui.Button("Stop & Report",
                                      clicked_fn=ext._stop_fps_monitor,
                                      height=30, style=S)
                        ui.Label("Monitor status: idle", height=20,
                                 style={"color": 0xFF88CCAA})
                        ext._diagnostics_status = ui.Label(
                            "Ready", height=40, style={"color": 0xFFAAAAAA})

                # ── System Status ─────────────────────────────────────────
                ui.Separator()
                ui.Label("System Status:", style={"font_size": 14, "color": 0xFF999999})
                ui.Label(
                    "RapidXPy: available" if rapidx_available
                    else "RapidXPy: not found -pip install rapidxpy",
                    style={"color": 0xFF77AA77 if rapidx_available else 0xFFAA7777})
                ui.Label(
                    "RapidXPy core: available" if core_available
                    else "RapidXPy core: not found",
                    style={"color": 0xFF77AA77 if core_available else 0xFFAAAA44})
                ext._general_status = ui.Label(
                    "System Ready", height=25, style={"color": 0xFF88CCAA})

    ext._window.visible = True
    ext._window.focus()