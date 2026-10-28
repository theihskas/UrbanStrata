"""
extension.py - RapidXPy Traffic Digital Twin (Isaac Sim / Omniverse Kit)
========================================================================
Entry point. Callback logic lives in mixin_*.py files:
  mixin_cesium.py   - Cesium setup and camera
  mixin_network.py  - network loading, buildings, travel times
  mixin_planning.py - traffic planning, transit, metrics viewports
  mixin_analysis.py - noise analysis and traffic visualisation
  mixin_nd.py       - Network Design Module
"""

# -- sys.path: add extension root so bundled rapidxpy/ is importable
import sys
import os as _os

_EXT_ROOT = _os.path.normpath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
if _EXT_ROOT not in sys.path:
    sys.path.insert(0, _EXT_ROOT)
    print(f"[my_rapidex] Added bundled rapidxpy root: {_EXT_ROOT}")

# -- Omniverse / Kit imports
import omni.ext
import omni.ui as ui
import omni.kit.pipapi
import omni.usd
import omni.kit.commands
import omni.kit.selection  # noqa: F401

try:
    import omni.kit.converter.geojson  # noqa: F401
except ImportError:
    pass

import os
import asyncio
import carb

# -- RapidXPy imports are deferred to on_startup() so they run after all
#    other extensions (and their PyTorch instances) have finished loading,
#    avoiding C10 dispatcher crashes from duplicate torch registrations.
_RAPIDX_DEPS = [
    ("pandas", "pandas"), ("osmnx", "osmnx"), ("geopandas", "geopandas"),
    ("h3", "h3"), ("networkx", "networkx"), ("shapely", "shapely"),
    ("pyproj", "pyproj"), ("scipy", "scipy"), ("geopy", "geopy"),
    ("requests", "requests"), ("rasterstats", "rasterstats"),
    ("reverse_geocode", "reverse_geocode"), ("contextily", "contextily"),
    ("pycountry", "pycountry"), ("timezonefinder", "timezonefinder"),
    ("sklearn", "scikit-learn"), ("pytz", "pytz"), ("gql", "gql"),
    ("requests_toolbelt", "requests-toolbelt"), ("dotenv", "python-dotenv"),
    ("googleapiclient", "google-api-python-client"),
]

# -- Mixin imports
from my_rapidex.my_python_ui_extension.mixin_cesium   import CesiumMixin
from my_rapidex.my_python_ui_extension.mixin_network  import NetworkMixin
from my_rapidex.my_python_ui_extension.mixin_planning import PlanningMixin
from my_rapidex.my_python_ui_extension.mixin_analysis import AnalysisMixin
from my_rapidex.my_python_ui_extension.mixin_nd       import NetworkDesignMixin
import my_rapidex.my_python_ui_extension.fps_diagnostics as _fps_diag
import my_rapidex.my_python_ui_extension.ai_ops          as _ai_ops
import my_rapidex.my_python_ui_extension.ui_builder      as _ui_builder


class RapidxPyOmniverseExtension(
    CesiumMixin, NetworkMixin, PlanningMixin, AnalysisMixin, NetworkDesignMixin,
    omni.ext.IExt,
):
    """Traffic Modeling Digital Twin."""

    def on_startup(self, ext_id):
        print("=== RapidxPy Omniverse Extension Starting ===")

        # Deferred pip installs — run after module import so other extensions
        # have already registered their torch instances before we touch them.
        for _imp, _pkg in _RAPIDX_DEPS:
            try:
                __import__(_imp)
            except ImportError:
                try:
                    omni.kit.pipapi.install(_pkg, ignore_import_check=True)
                except Exception as _pip_exc:
                    print(f"[my_rapidex] WARNING: could not install {_pkg}: {_pip_exc}")

        # Deferred RapidXPy import
        _rapidxpy_available    = False
        _rapidx_core_available = False
        _rapidx_network        = None
        _rapidx_traveltime     = None
        try:
            import rapidxpy as rx  # noqa: F401
            from rapidxpy.scripts import network    as _rn
            from rapidxpy.scripts import traveltime as _rt
            _rapidx_network    = _rn
            _rapidx_traveltime = _rt
            _rapidxpy_available = True
            carb.log_info("[my_rapidex] RapidXPy imported successfully")
        except Exception as _e:
            import traceback as _tb
            carb.log_warn(f"[my_rapidex] RapidXPy import failed: {_e}\n{_tb.format_exc()}")

        try:
            from rapidxpy.scripts.core import accessibility  # noqa: F401
            _rapidx_core_available = True
        except Exception:
            pass

        # Expose RapidXPy refs so mixins can read via self
        self._rapidxpy_available      = _rapidxpy_available
        self._rapidxpy_core_available = _rapidx_core_available
        self._rapidx_network          = _rapidx_network
        self._rapidx_traveltime       = _rapidx_traveltime

        # Core state
        self._window          = None
        self._city_name       = "Dresden, Germany"
        self._base_path       = "D:/kit-exts-project3"
        self._network_folder  = None
        self._current_city    = None
        self._otp_process     = None
        self._link_width      = 3.0
        self._node_radius     = 1.0
        self._boundary_width  = 1.0
        self._link_color      = (0.0, 0.5, 1.0)
        self._node_color      = (1.0, 0.2, 0.2)
        self._boundary_color  = (0.2, 0.8, 0.2)
        self._mapbox_api_key  = ""
        self._llm_api_key     = ""

        # Network Design Module state
        self._nd_edits          = {}
        self._nd_selected_idx   = None
        self._nd_edit_window    = None
        self._nd_selection_mode = False
        self._nd_modified_csv   = None
        self._nd_compare_window = None
        self._nd_selection_sub  = None
        self._pending_futures   = []

        # Noise band selection state
        self._noise_info_window   = None
        self._noise_selection_sub = None

        # Load API keys from bundled .env
        _env_path = os.path.join(_EXT_ROOT, "rapidxpy", ".env")
        if os.path.exists(_env_path):
            try:
                with open(_env_path) as _ef:
                    for _line in _ef:
                        _line = _line.strip()
                        if "=" in _line and not _line.startswith("#"):
                            _k, _v = _line.split("=", 1)
                            _v = _v.strip()
                            if _v:
                                os.environ[_k.strip()] = _v
                self._mapbox_api_key = (os.environ.get("MAPBOX_API_KEYS", "") or
                                        os.environ.get("MAPBOX_ACCESS_TOKEN", ""))
                for _ek, _ev in {
                    "TOMTOM_API_KEY": os.environ.get("TOMTOM_API_KEYS", ""),
                    "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEYS", ""),
                }.items():
                    if _ev:
                        os.environ[_ek] = _ev
                carb.log_info(f"[my_rapidex] Loaded API keys from {_env_path}")
            except Exception as _env_exc:
                carb.log_warn(f"[my_rapidex] Could not load .env: {_env_exc}")

        _ui_builder.build_main_window(self, _rapidxpy_available, _rapidx_core_available)

        _prefill = (os.environ.get("TOMTOM_API_KEYS", "") or
                    os.environ.get("MAPBOX_API_KEYS", "") or
                    os.environ.get("GOOGLE_API_KEYS", ""))
        if _prefill:
            try:
                self._travel_api_key_input.model.set_value(_prefill)
            except Exception:
                pass

        try:
            self._nd_selection_sub = (
                omni.usd.get_context()
                .get_stage_event_stream()
                .create_subscription_to_pop(
                    self._nd_on_stage_event,
                    name="rapidx_network_design_selection",
                )
            )
        except Exception as _nd_exc:
            carb.log_warn(f"[network_design] Could not subscribe: {_nd_exc}")

        try:
            self._noise_selection_sub = (
                omni.usd.get_context()
                .get_stage_event_stream()
                .create_subscription_to_pop(
                    self._noise_on_stage_event,
                    name="rapidx_noise_band_selection",
                )
            )
        except Exception as _noise_exc:
            carb.log_warn(f"[noise_viz] Could not subscribe to selection: {_noise_exc}")

        self._run_async(self._startup_cesium())
        print("=== RapidxPy Omniverse Extension Ready ===")

    def on_shutdown(self):
        print("=== RapidxPy Omniverse Extension Shutdown ===")
        if getattr(self, "_traffic_animator", None):
            self._traffic_animator.stop()
            self._traffic_animator = None
        if getattr(self, "_nd_selection_sub", None):
            self._nd_selection_sub = None
        if getattr(self, "_noise_selection_sub", None):
            self._noise_selection_sub = None
        for _attr in ("_nd_edit_window", "_nd_compare_window", "_noise_info_window"):
            _win = getattr(self, _attr, None)
            if _win:
                try:
                    _win.destroy()
                except Exception:
                    pass
                setattr(self, _attr, None)
        for fut in getattr(self, "_pending_futures", []):
            try:
                fut.cancel()
            except Exception:
                pass
        self._pending_futures = []
        if self._window:
            self._window.destroy()
            self._window = None

    def _run_async(self, coro):
        fut = asyncio.ensure_future(coro)
        self._pending_futures.append(fut)
        def _cleanup(f):
            try:
                self._pending_futures.remove(f)
            except (ValueError, AttributeError):
                pass
        fut.add_done_callback(_cleanup)
        return fut

    # -- Status label helpers

    def _set_status(self, msg):
        try:
            self._general_status.text = msg
        except Exception:
            pass
        carb.log_info(f"[my_rapidex] {msg}")

    def _set_network_status(self, msg):
        try:
            self._network_status.text = f"Status: {msg}"
        except Exception:
            pass

    def _set_travel_time_status(self, msg):
        try:
            self._travel_time_status.text = f"Travel Times: {msg}"
        except Exception:
            pass

    def _set_planning_status(self, msg):
        try:
            self._planning_status.text = f"Planning: {msg}"
        except Exception:
            pass

    def _set_traffic_viz_status(self, msg):
        try:
            self._traffic_viz_status.text = f"Viz: {msg}"
        except Exception:
            pass
        carb.log_info(f"[traffic_viz] {msg}")

    # -- Performance Diagnostics

    def _start_fps_monitor(self):
        try:
            self._diagnostics_status.text = "Monitor running... (5 minutes)"
            _fps_diag.start_fps_monitor(duration_sec=300, sample_interval=1.0)
        except Exception as e:
            self._diagnostics_status.text = f"Error: {e}"

    def _stop_fps_monitor(self):
        try:
            _fps_diag.stop_fps_monitor()
            self._diagnostics_status.text = "Report generated (check console)"
        except Exception as e:
            self._diagnostics_status.text = f"Error: {e}"

    # -- AI chatbot

    def _send_chatbot_message(self):
        query = self._chatbot_input.model.get_value_as_string().strip()
        if not query:
            return
        model = self._llm_api_key_input.model.get_value_as_string().strip() or "llama3.2"
        self._chatbot_response.text = f"Thinking... (model: {model})"
        city_name, _ = self._get_project_dir()
        self._run_async(self._send_chatbot_message_async(query, model, city_name))

    async def _send_chatbot_message_async(self, query, model, city_name):
        loop = asyncio.get_event_loop()
        try:
            answer = await loop.run_in_executor(
                None, lambda: _ai_ops.query_chatbot(query, model, city_name))
            self._chatbot_response.text = answer[:800]
        except Exception as exc:
            err = str(exc)
            if "Connection refused" in err or "Failed to establish" in err:
                self._chatbot_response.text = (
                    "Ollama is not running.\nStart: ollama serve\nPull: ollama pull llama3.2")
            else:
                self._chatbot_response.text = f"Error: {err[:300]}"

    # -- AI Command Interface

    def _execute_ai_command(self):
        cmd = self._ai_cmd_input.model.get_value_as_string().strip()
        if not cmd:
            return
        model = self._ai_cmd_model_input.model.get_value_as_string().strip() or "llama3.2"
        self._ai_cmd_status.text = f"Parsing: '{cmd}'..."
        self._run_async(self._execute_ai_command_async(cmd, model))

    async def _execute_ai_command_async(self, cmd, model):
        loop      = asyncio.get_event_loop()
        city_name = self._city_input.model.get_value_as_string() or self._city_name
        base_path = self._base_path_input.model.get_value_as_string()
        try:
            parsed = await loop.run_in_executor(
                None, lambda: _ai_ops.parse_ai_command(cmd, model, city_name, base_path))
            await self._run_parsed_command(parsed)
        except Exception as exc:
            err = str(exc)
            if "Connection refused" in err or "Failed to establish" in err:
                self._ai_cmd_status.text = "Ollama not running. Start: ollama serve"
            else:
                self._ai_cmd_status.text = f"Error: {err[:250]}"

    async def _run_parsed_command(self, parsed):
        import json as _json
        action = parsed.get("action", "unknown")
        if action == "set_city":
            city = parsed.get("city", "").strip()
            if city:
                self._city_input.model.set_value(city)
                self._city_name = city
                self._ai_cmd_status.text = f"City set to: {city}"
            else:
                self._ai_cmd_status.text = "No city name found."
        elif action in ("download_network", "build_network"):
            city = parsed.get("city", "").strip()
            if city:
                self._city_input.model.set_value(city)
                self._city_name = city
            self._ai_cmd_status.text = f"Building network: {city or self._city_name}"
            self._build_network()
        elif action == "load_network":
            self._ai_cmd_status.text = "Loading network into stage..."
            self._load_network()
        elif action == "apply_zoning":
            zone_type  = parsed.get("zone_type", "H3")
            resolution = int(parsed.get("resolution", 7))
            self._zone_type_selector.model.get_item_value_model().set_value(
                0 if zone_type.upper() == "H3" else 1)
            self._zone_resolution_input.model.set_value(resolution)
            self._ai_cmd_status.text = f"Applying {zone_type} zoning (res {resolution})..."
            self._apply_zoning()
        elif action == "fetch_travel_times":
            api = parsed.get("api", "tomtom").lower()
            self._api_selector.model.get_item_value_model().set_value(
                {"tomtom": 0, "google": 1, "mapbox": 2}.get(api, 0))
            self._ai_cmd_status.text = f"Fetching travel times via {api}..."
            self._fetch_travel_times()
        elif action == "fix_network":
            self._ai_cmd_status.text = "Fixing network speeds..."
            self._fix_network()
        elif action == "unknown":
            self._ai_cmd_status.text = parsed.get("message", "Command not recognised.")
        else:
            self._ai_cmd_status.text = f"Unhandled: {_json.dumps(parsed)}"
