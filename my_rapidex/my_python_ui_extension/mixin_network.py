"""
mixin_network.py — Network loading, downloading, and travel-time callbacks.
"""
import os
import asyncio
from datetime import datetime
from argparse import Namespace
import carb
import omni.usd

from my_rapidex.my_python_ui_extension.geojson_usd_writer import (
    convert_network_folder_to_usd, convert_zones_folder_to_usd,
    convert_buildings_to_usd, apply_sublayers,
    compute_buildings_batch_geometry, write_buildings_usd_from_geometry,
)
from my_rapidex.my_python_ui_extension.network_ops import (
    get_city_coordinates, get_city_elevation,
    download_network, apply_zoning, fix_network_speeds,
)
from my_rapidex.my_python_ui_extension.building_ops import download_buildings


class NetworkMixin:

    # ── Project helpers ───────────────────────────────────────────────────────

    def _get_project_dir(self):
        city_name = self._city_input.model.get_value_as_string()
        base = self._base_path_input.model.get_value_as_string() or self._base_path
        safe = (city_name.replace(" ", "_").replace(",", "")
                         .replace("(", "").replace(")", ""))
        return city_name, os.path.join(base, safe)

    def _get_ui_network_params(self):
        road_types = [r.strip() for r in
                      self._road_types_input.model.get_value_as_string().split(",")
                      if r.strip()]
        speeds = [int(s.strip()) for s in
                  self._default_speed_input.model.get_value_as_string().split(",")
                  if s.strip().isdigit()]
        zone_idx  = self._zone_type_selector.model.get_item_value_model().get_value_as_int()
        zone_type = ["H3", "Square"][zone_idx]
        zone_res  = self._zone_resolution_input.model.get_value_as_int()
        zone_lvls = self._zone_levels_input.model.get_value_as_int()
        zone_file = self._zone_file_input.model.get_value_as_string() or None
        return road_types, speeds, zone_type, zone_res, zone_lvls, zone_file

    @staticmethod
    def _resolve_network_folder(project_dir):
        candidate = os.path.join(project_dir, "Network")
        if os.path.exists(os.path.join(candidate, "edges.geojson")):
            return candidate
        try:
            for entry in os.scandir(project_dir):
                if not entry.is_dir():
                    continue
                alt = os.path.join(entry.path, "Network")
                if os.path.exists(os.path.join(alt, "edges.geojson")):
                    carb.log_info(f"[my_rapidex] Found network in double-dir: {alt}")
                    return alt
        except Exception:
            pass
        return None

    @staticmethod
    def _find_zones_geojson(project_dir):
        for sub in ("Zones_H3", "Zones_default"):
            p = os.path.join(project_dir, sub, "Zones.geojson")
            if os.path.exists(p):
                return p
        return os.path.join(project_dir, "Zones_H3", "Zones.geojson")

    # ── Network ───────────────────────────────────────────────────────────────

    def _build_network(self):
        city_name, project_dir = self._get_project_dir()
        if not city_name:
            return self._set_network_status("Please enter a city name")
        network_folder = self._resolve_network_folder(project_dir)
        if network_folder:
            self._set_network_status("Network files found - converting to 3D USD...")
            self._network_folder = network_folder
            self._current_city   = city_name
            self._run_async(self._convert_and_load_async())
            return
        if not self._rapidxpy_available:
            return self._set_network_status("RapidXPy not available — place GeoJSON in Network/ manually")
        road_types, speeds, _, _, _, _ = self._get_ui_network_params()
        self._set_network_status(f"Downloading network for {city_name}...")
        self._run_async(self._build_network_async(city_name, project_dir, road_types, speeds))

    async def _build_network_async(self, city_name, project_dir, road_types, speeds):
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: download_network(city_name, project_dir, road_types, speeds, self._rapidx_network))
        if not ok:
            return self._set_network_status(msg)
        network_folder = self._resolve_network_folder(project_dir)
        if network_folder:
            self._network_folder = network_folder
            self._current_city   = city_name
            self._set_network_status("Download complete - converting to 3D USD...")
            await self._convert_and_load_async()
        else:
            self._set_network_status("Download finished but edges.geojson not found")

    def _download_network(self):
        if not self._rapidxpy_available:
            return self._set_network_status("RapidXPy not available")
        city_name, project_dir = self._get_project_dir()
        if not city_name:
            return self._set_network_status("Please enter a city name")
        road_types, speeds, _, _, _, _ = self._get_ui_network_params()
        self._set_network_status(f"Downloading network for {city_name}...")
        self._run_async(self._download_network_async(city_name, project_dir, road_types, speeds))

    async def _download_network_async(self, city_name, project_dir, road_types, speeds):
        loop = asyncio.get_event_loop()
        ok, msg = await loop.run_in_executor(
            None, lambda: download_network(city_name, project_dir, road_types, speeds, self._rapidx_network))
        if ok:
            self._current_city = city_name
            found = self._resolve_network_folder(project_dir)
            if found:
                self._network_folder = found
        self._set_network_status(msg)

    def _apply_zoning(self):
        if not self._rapidxpy_available:
            return self._set_network_status("RapidXPy not available")
        city_name, project_dir = self._get_project_dir()
        if not city_name:
            return self._set_network_status("Please enter a city name")
        _, _, zone_type, zone_res, zone_levels, zone_file = self._get_ui_network_params()
        network_folder = self._resolve_network_folder(project_dir)
        effective_dir  = os.path.dirname(network_folder) if network_folder else project_dir
        self._set_network_status(f"Applying {zone_type} zoning...")
        self._run_async(self._apply_zoning_async(city_name, effective_dir, zone_type, zone_res, zone_levels, zone_file))

    async def _apply_zoning_async(self, city_name, effective_dir, zone_type, zone_res, zone_levels, zone_file):
        loop = asyncio.get_event_loop()
        ok, msg, zones_folder = await loop.run_in_executor(
            None, lambda: apply_zoning(city_name, effective_dir, zone_type, zone_res, zone_levels, zone_file))
        self._set_network_status(msg)
        if ok and zones_folder and os.path.isdir(zones_folder):
            self._set_network_status(f"{msg} — loading zones...")
            lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
            usd_dir = os.path.join(effective_dir, "Zones_USD")
            usd_paths, load_msg = await loop.run_in_executor(
                None, lambda: convert_zones_folder_to_usd(zones_folder, lat, lon, usd_dir))
            stage = omni.usd.get_context().get_stage()
            apply_sublayers(stage, usd_paths)
            self._set_network_status(f"{msg} | {load_msg}")

    def _load_network(self):
        city_name, project_dir = self._get_project_dir()
        if not city_name:
            return self._set_network_status("Please enter a city name")
        network_folder = self._resolve_network_folder(project_dir)
        if not network_folder:
            return self._set_network_status(f"edges.geojson not found under {project_dir}")
        self._network_folder = network_folder
        self._current_city   = city_name
        self._run_async(self._convert_and_load_async())

    async def _convert_and_load_async(self):
        loop      = asyncio.get_event_loop()
        city_name = self._current_city or "City"
        elev      = get_city_elevation(city_name)
        lat, lon  = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))
        self._update_cesium_georeference(lat, lon, elev)
        usd_dir = os.path.join(os.path.dirname(self._network_folder), "Network_USD")
        usd_paths, msg = await loop.run_in_executor(
            None, lambda: convert_network_folder_to_usd(self._network_folder, lat, lon, usd_dir))
        stage = omni.usd.get_context().get_stage()
        apply_sublayers(stage, usd_paths)
        self._set_network_status(msg)

    # ── Fix network ───────────────────────────────────────────────────────────

    def _fix_network(self):
        if not self._rapidxpy_available:
            self._fix_status.text = "RapidXPy not available"
            return
        _, project_dir = self._get_project_dir()
        city_name = self._city_input.model.get_value_as_string()
        self._fix_status.text = "Fixing network speeds..."
        self._run_async(self._fix_network_async(city_name, project_dir))

    async def _fix_network_async(self, city_name, project_dir):
        loop = asyncio.get_event_loop()
        _, msg = await loop.run_in_executor(
            None, lambda: fix_network_speeds(city_name, project_dir, self._rapidx_network))
        self._fix_status.text = msg

    # ── Travel times ──────────────────────────────────────────────────────────

    def _fetch_travel_times(self):
        city_name = self._city_input.model.get_value_as_string()
        if not city_name:
            return self._set_status("Please enter a city name")
        if not self._rapidxpy_available:
            return self._set_status("RapidXPy not available")

        api_names = ["tomtom", "google", "mapbox"]
        api = api_names[self._api_selector.model.get_item_value_model().get_value_as_int()]

        hours_str = self._tt_hours_input.model.get_value_as_string().strip()
        if hours_str == "now":
            hours = [datetime.now().hour]
        elif hours_str == "all":
            hours = list(range(24))
        else:
            hours = [int(h.strip()) for h in hours_str.split(",") if h.strip().isdigit()] or [datetime.now().hour]

        date_range = self._tt_date_range_input.model.get_value_as_int()
        min_length = self._tt_min_length_input.model.get_value_as_float()
        _, project_dir = self._get_project_dir()
        if not os.path.exists(project_dir):
            return self._set_travel_time_status(f"Project dir not found: {project_dir}")

        network_folder = self._network_folder or self._resolve_network_folder(project_dir)
        if not network_folder or not os.path.isdir(network_folder):
            return self._set_travel_time_status("Network folder not found — load network first")

        tt_folder = os.path.join(project_dir, "Traveltimes")
        os.makedirs(tt_folder, exist_ok=True)

        api_key = self._travel_api_key_input.model.get_value_as_string().strip()
        if not api_key:
            api_key = (os.environ.get("TOMTOM_API_KEYS", "") or
                       os.environ.get("MAPBOX_API_KEYS", "") or
                       os.environ.get("MAPBOX_ACCESS_TOKEN", "") or
                       os.environ.get("GOOGLE_API_KEYS", ""))
        if api_key:
            for k in ("TOMTOM_API_KEY","TOMTOM_API_KEYS","GOOGLE_API_KEY","MAPBOX_ACCESS_TOKEN","MAPBOX_API_KEYS"):
                os.environ[k] = api_key

        now = datetime.now()
        self._set_travel_time_status(f"Fetching via {api.upper()}...")
        self._run_async(self._fetch_travel_times_async(api, hours, date_range, min_length, tt_folder, network_folder, now))

    async def _fetch_travel_times_async(self, api, hours, date_range, min_length, tt_folder, network_folder, started_at):
        loop = asyncio.get_event_loop()
        def _do():
            self._rapidx_traveltime.fetch_travel_times(Namespace(
                api=api, hours=hours,
                save_folders=[tt_folder], paths=[network_folder],
                date_range=date_range, min_length=min_length,
                fix=True, verbose=False,
            ))
        try:
            await loop.run_in_executor(None, _do)
            self._set_travel_time_status(
                f"{api.upper()} fetched at {started_at.strftime('%H:%M')} (hours={hours})")
        except Exception as exc:
            self._set_travel_time_status(f"Error: {exc}")
            carb.log_error(f"[traveltime] {exc}")

    # ── OSM Buildings ─────────────────────────────────────────────────────────

    def _load_osm_buildings(self):
        city_name, project_dir = self._get_project_dir()
        if not city_name:
            self._network_status.text = "Set a city name first."
            return
        mb_val = self._buildings_max_model.get_value_as_int()
        max_buildings = mb_val if mb_val > 0 else 1000000
        noise_geojson = self._buildings_noise_input.model.get_value_as_string().strip()
        if noise_geojson and not os.path.exists(noise_geojson):
            auto = os.path.join(project_dir, "Noise", "noise_decay_dissolved.geojson")
            if os.path.exists(auto):
                noise_geojson = auto
            else:
                self._network_status.text = f"Noise GeoJSON not found: {noise_geojson}"
                return
        elif not noise_geojson:
            auto = os.path.join(project_dir, "Noise", "noise_decay_dissolved.geojson")
            if os.path.exists(auto):
                noise_geojson = auto
        output_dir = os.path.join(project_dir, "Buildings")
        self._network_status.text = f"Downloading OSM buildings for {city_name} (max {max_buildings})..."
        self._run_async(self._load_osm_buildings_async(
            city_name, output_dir, noise_geojson or None, max_buildings=max_buildings))

    async def _load_osm_buildings_async(self, city_name, output_dir, noise_geojson, max_buildings=3000):
        import glob as _glob
        import math as _math
        loop = asyncio.get_event_loop()

        geojson_path = os.path.join(output_dir, "buildings.geojson")
        usd_dir      = os.path.join(output_dir, "Buildings_USD")

        if os.path.exists(geojson_path):
            self._network_status.text = "Using cached buildings.geojson — skipping download."
        else:
            ok, msg, geojson_path = await loop.run_in_executor(
                None, lambda: download_buildings(city_name, output_dir,
                                                 noise_geojson=noise_geojson,
                                                 max_buildings=max_buildings))
            if not ok:
                self._network_status.text = msg
                return
            self._network_status.text = f"{msg} - geocoding..."

        lat, lon = await loop.run_in_executor(None, lambda: get_city_coordinates(city_name))

        # Invalidate stale USD cache (from a previous run with different max_buildings)
        existing_usd  = sorted(_glob.glob(os.path.join(usd_dir, "buildings*.usd")))
        _batch_size   = 500   # must match convert_buildings_to_usd default
        _expected_max = max(1, _math.ceil(max_buildings / _batch_size) + 1)
        if existing_usd and len(existing_usd) > _expected_max:
            carb.log_warn(f"[buildings] Stale USD cache ({len(existing_usd)} files > {_expected_max}), regenerating")
            for _stale in existing_usd:
                try:
                    os.remove(_stale)
                except Exception:
                    pass
            existing_usd = []

        import omni.kit.app as _kit_app
        stage     = omni.usd.get_context().get_stage()
        root_layer = stage.GetRootLayer()

        # Remove any previously loaded building sublayers from the stage BEFORE
        # writing new USD files.  Writing to a layer that is still referenced by
        # the stage compositor from a background thread causes a hard crash.
        _usd_dir_norm = usd_dir.replace("\\", "/")
        _stale_subs   = [p for p in list(root_layer.subLayerPaths)
                         if "buildings" in p.replace("\\", "/").split("/")[-1]
                         and _usd_dir_norm in p.replace("\\", "/")]
        for _sp in _stale_subs:
            try:
                root_layer.subLayerPaths.remove(_sp)
            except Exception:
                pass
        if _stale_subs:
            # Let the stage settle before we write new files.
            for _ in range(3):
                await _kit_app.get_app().next_update_async()

        if existing_usd:
            usd_paths = [p.replace("\\", "/") for p in existing_usd]
            load_msg  = f"Using {len(usd_paths)} cached buildings USD file(s)."
            self._network_status.text = load_msg
            for i, p in enumerate(usd_paths):
                while p in root_layer.subLayerPaths:
                    root_layer.subLayerPaths.remove(p)
                root_layer.subLayerPaths.append(p)
                current_loaded = min((i + 1) * _batch_size, max_buildings)
                total_expected = min(len(usd_paths) * _batch_size, max_buildings)
                self._network_status.text = f"Loading buildings into stage: ~{current_loaded}/{total_expected} (batch {i+1}/{len(usd_paths)})..."
                for _ in range(5):
                    await _kit_app.get_app().next_update_async()
            
            msg = f"OSM Buildings loaded — ~{total_expected} building prims in {len(usd_paths)} USD sublayer(s)"
            self._network_status.text = msg
            carb.log_info(f"[geojson_usd_writer] {msg}")
            print(f"[geojson_usd_writer] {msg}")
        else:
            # Phase 1: read GeoJSON in background (pure file I/O, no Sdf).
            import json as _json
            self._network_status.text = "Reading buildings GeoJSON..."

            def _read_geojson():
                with open(geojson_path, "r", encoding="utf-8") as _fh:
                    return _json.load(_fh).get("features", [])

            all_features = await loop.run_in_executor(None, _read_geojson)
            if max_buildings and len(all_features) > max_buildings:
                all_features = all_features[:max_buildings]

            os.makedirs(usd_dir, exist_ok=True)
            _bsize   = 500
            batches  = [(si, all_features[si: si + _bsize])
                        for si in range(0, max(1, len(all_features)), _bsize)]
            total_prims = 0

            for batch_num, (start_idx, batch) in enumerate(batches):
                # Phase 2: compute geometry in background thread — no Sdf, thread-safe.
                self._network_status.text = (
                    f"Computing buildings geometry {start_idx}/{len(all_features)} (batch {batch_num+1}/{len(batches)})...")
                geom_list = await loop.run_in_executor(
                    None, lambda b=batch, s=start_idx:
                    compute_buildings_batch_geometry(b, s, lat, lon))

                # Phase 3: write USD layer on main thread — Sdf is not thread-safe.
                suffix   = f"_part{batch_num:02d}" if len(batches) > 1 else ""
                usd_path = os.path.join(usd_dir, f"buildings{suffix}.usd").replace("\\", "/")
                created  = write_buildings_usd_from_geometry(usd_path, geom_list)
                total_prims += created
                carb.log_info(
                    f"[buildings] batch {batch_num+1}/{len(batches)}: "
                    f"{created} prims → {os.path.basename(usd_path)}")

                # Add sublayer immediately so Hydra streams geometry progressively.
                while usd_path in root_layer.subLayerPaths:
                    root_layer.subLayerPaths.remove(usd_path)
                root_layer.subLayerPaths.append(usd_path)
                self._network_status.text = (
                    f"Loading buildings into stage: {total_prims}/{len(all_features)} (batch {batch_num+1}/{len(batches)})...")
                for _ in range(3):
                    await _kit_app.get_app().next_update_async()

            msg = f"OSM Buildings loaded — {total_prims} building prims in {len(batches)} USD sublayer(s)"
            self._network_status.text = msg
            carb.log_info(f"[geojson_usd_writer] {msg}")
            print(f"[geojson_usd_writer] {msg}")