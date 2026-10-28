"""
STEP 7 — Interactive Traffic Visualisation Dashboard
======================================================
Opens a floating omni.ui control panel for live interaction with the
Dresden digital twin scene.
Run in Isaac Sim Script Editor LAST, after all other scripts have been run.

What this script does:
  • Creates a dockable "Traffic Visualization Dashboard" window (420 × 880 px)
    with collapsible sections for each visualisation layer.
  • Animation Playback section:
      Play / Pause / Stop / Rewind buttons
      Speed slider (0.1× – 5×) applied via omni.timeline
  • Per-layer sections (Road Pipes, Traffic Flow, Intersection Nodes,
    Zone Demand, Noise Decay, Cesium Tile Colouring):
      Visibility checkbox — shows/hides the layer group on the stage
      Live opacity and emissive sliders — walk every Shader prim under
        the group and update the OmniPBR inputs in real time
      Parameter fields (volume divisor, column height, pulse boost, etc.)
      Regenerate button — re-executes the corresponding generation script
        with the dashboard parameter values injected, replacing the layer
        in the scene without restarting Isaac Sim
  • Zone Demand section has separate Origin / Destination checkboxes to
    toggle the two demand views independently.
  • Cesium Tile Colouring section has an Origin / Destination radio button
    and a "Color Tiles (snapshot)" button — re-run after moving the camera.

Usage:
  Run once to open the panel. Re-running closes the previous instance
  automatically and opens a fresh one with default values.

Works with the scene built by (in order):
  1. create_roads_from_geojson_direct2.py
  2. create_traffic_flow_in_pipes2.py
  3. create_animated_nodes.py
  4. create_zone_visualization.py
  5. color_cesium_tiles_by_zone.py
  6. create_noise_decay.py
"""

import omni.usd
import omni.ui as ui
import omni.timeline
from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf
import math, os

# ── Resolve scene root ────────────────────────────────────────

_stage = omni.usd.get_context().get_stage()
_root = "/World"
for _try in ["/CesiumGeoreference", "/World/CesiumGeoreference"]:
    if _stage.GetPrimAtPath(_try).IsValid():
        _root = _try
        break

# Known group paths (created by the various scripts)
PATHS = {
    "pipes":        f"{_root}/edges",
    "flow":         f"{_root}/edges/TrafficFlow",
    "nodes":        f"{_root}/AnimatedNodes",
    "zones":        f"{_root}/Zones",
    "zones_origin": f"{_root}/Zones/OriginDemand",
    "zones_dest":   f"{_root}/Zones/DestDemand",
    "noise":        f"{_root}/NoiseDecay",
}

# Script file paths (for regeneration)
# EDIT THIS LINE — point to your "accessibility raw data" folder
_DATA = r"C:\Omniverse\projects\Interactive-digital-twin-for-traffic-modelling-and-rapid-planning\accessibility raw data"
_HERE = os.path.dirname(_DATA)  # project root — where the scripts live
SCRIPTS = {
    "pipes":  os.path.join(_HERE, "create_roads_from_geojson_direct2.py"),
    "flow":   os.path.join(_HERE, "create_traffic_flow_in_pipes2.py"),
    "nodes":  os.path.join(_HERE, "create_animated_nodes.py"),
    "zones":  os.path.join(_HERE, "create_zone_visualization.py"),
    "tiles":  os.path.join(_HERE, "color_cesium_tiles_by_zone.py"),
    "noise":  os.path.join(_HERE, "create_noise_decay.py"),
}


# ── Scene helpers ─────────────────────────────────────────────

def _get_stage():
    return omni.usd.get_context().get_stage()


def _prim_exists(path):
    p = _get_stage().GetPrimAtPath(path)
    return p and p.IsValid()


def _set_visible(path, visible):
    """Toggle visibility of a group prim."""
    prim = _get_stage().GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return
    img = UsdGeom.Imageable(prim)
    if visible:
        img.MakeVisible()
    else:
        img.MakeInvisible()


def _update_shaders(group_path, prop_name, value):
    """Walk every Shader under *group_path* and set an input."""
    stage = _get_stage()
    root = stage.GetPrimAtPath(group_path)
    if not root or not root.IsValid():
        return
    for prim in Usd.PrimRange(root):
        if prim.GetTypeName() == "Shader":
            shader = UsdShade.Shader(prim)
            inp = shader.GetInput(prop_name)
            if inp:
                inp.Set(value)


def _run_script(path):
    """Execute one of the generation scripts."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        exec(compile(code, path, "exec"), {"__name__": "__main__"})
    except Exception as e:
        print(f"ERROR running {path}: {e}")


# ── Dashboard class ───────────────────────────────────────────

class TrafficDashboard:

    def __init__(self):
        self._timeline = omni.timeline.get_timeline_interface()

        # Defaults (match the generation scripts)
        self._pipe_opacity   = 0.18
        self._pipe_emissive  = 0.3
        self._flow_emissive  = 5.0
        self._node_opacity   = 0.85
        self._node_emissive  = 4.0
        self._zone_opacity   = 0.18
        self._zone_emissive  = 0.6
        self._noise_opacity  = 0.22
        self._noise_emissive = 1.0
        self._playback_speed = 1.0

        self._build_window()

    # ── Window ────────────────────────────────────────────────

    def _build_window(self):
        self._window = ui.Window(
            "Traffic Visualization Dashboard",
            width=420,
            height=880,
        )
        with self._window.frame:
            with ui.ScrollingFrame():
                with ui.VStack(spacing=6, style={"margin": 6}):
                    self._section_playback()
                    self._section_pipes()
                    self._section_flow()
                    self._section_nodes()
                    self._section_zones()
                    self._section_noise()
                    self._section_tiles()

    # ── 1. Animation Playback ─────────────────────────────────

    def _section_playback(self):
        with ui.CollapsableFrame("Animation Playback", collapsed=False):
            with ui.VStack(spacing=5):
                # Transport buttons
                with ui.HStack(height=30, spacing=4):
                    ui.Button("Play",
                              clicked_fn=lambda: self._timeline.play(),
                              height=28)
                    ui.Button("Pause",
                              clicked_fn=lambda: self._timeline.pause(),
                              height=28)
                    ui.Button("Stop",
                              clicked_fn=self._stop_anim,
                              height=28)
                    ui.Button("Rewind",
                              clicked_fn=self._rewind,
                              height=28)

                # Speed slider
                with ui.HStack(height=22):
                    ui.Label("Speed:", width=60)
                    slider = ui.FloatSlider(min=0.1, max=5.0, height=20)
                    slider.model.set_value(self._playback_speed)
                    self._speed_label = ui.Label("1.0x", width=40)

                def _on_speed(model):
                    val = model.get_value_as_float()
                    self._playback_speed = val
                    self._speed_label.text = f"{val:.1f}x"
                    self._timeline.set_play_rate(val)

                slider.model.add_value_changed_fn(_on_speed)

    def _stop_anim(self):
        self._timeline.stop()

    def _rewind(self):
        self._timeline.set_current_time(0)

    # ── 2. Pipes (road network) ───────────────────────────────

    def _section_pipes(self):
        with ui.CollapsableFrame("Road Pipes", collapsed=False):
            with ui.VStack(spacing=5):
                # Visibility
                with ui.HStack(height=22):
                    cb = ui.CheckBox(width=20)
                    cb.model.set_value(True)
                    ui.Label("Visible")
                    cb.model.add_value_changed_fn(
                        lambda m: _set_visible(PATHS["pipes"],
                                               m.get_value_as_bool()))

                # Opacity
                self._slider_row("Pipe Opacity", 0.0, 1.0,
                                 self._pipe_opacity,
                                 self._on_pipe_opacity)
                # Emissive
                self._slider_row("Pipe Emissive", 0.0, 3.0,
                                 self._pipe_emissive,
                                 self._on_pipe_emissive)

                ui.Button("Regenerate Pipes",
                          clicked_fn=lambda: _run_script(SCRIPTS["pipes"]),
                          height=28)

    def _on_pipe_opacity(self, model):
        val = model.get_value_as_float()
        self._pipe_opacity = val
        _update_shaders(PATHS["pipes"], "opacity_constant", val)

    def _on_pipe_emissive(self, model):
        val = model.get_value_as_float()
        self._pipe_emissive = val
        _update_shaders(PATHS["pipes"], "emissive_intensity", val)

    # ── 3. Traffic Flow (particles) ───────────────────────────

    def _section_flow(self):
        with ui.CollapsableFrame("Traffic Flow Particles", collapsed=False):
            with ui.VStack(spacing=5):
                # Visibility
                with ui.HStack(height=22):
                    cb = ui.CheckBox(width=20)
                    cb.model.set_value(True)
                    ui.Label("Visible")
                    cb.model.add_value_changed_fn(
                        lambda m: _set_visible(PATHS["flow"],
                                               m.get_value_as_bool()))

                # Emissive intensity
                self._slider_row("Particle Glow", 0.0, 15.0,
                                 self._flow_emissive,
                                 self._on_flow_emissive)

                # Regeneration params
                ui.Separator()
                ui.Label("Regeneration Parameters:", height=20)

                with ui.HStack(height=22):
                    ui.Label("Volume Divisor:", width=120)
                    self._flow_vol_div = ui.FloatField(height=20)
                    self._flow_vol_div.model.set_value(150.0)

                with ui.HStack(height=22):
                    ui.Label("Max Particles:", width=120)
                    self._flow_max_p = ui.IntField(height=20)
                    self._flow_max_p.model.set_value(15)

                with ui.HStack(height=22):
                    ui.Label("Particle Radius:", width=120)
                    self._flow_radius = ui.FloatField(height=20)
                    self._flow_radius.model.set_value(1.5)

                ui.Button("Regenerate Flow",
                          clicked_fn=self._regen_flow,
                          height=28)

    def _on_flow_emissive(self, model):
        val = model.get_value_as_float()
        self._flow_emissive = val
        _update_shaders(PATHS["flow"], "emissive_intensity", val)

    def _regen_flow(self):
        """Re-run traffic flow script with dashboard params injected."""
        self._inject_and_run(SCRIPTS["flow"], {
            "VOLUME_DIVISOR":  self._flow_vol_div.model.get_value_as_float(),
            "MAX_PARTICLES":   self._flow_max_p.model.get_value_as_int(),
            "PARTICLE_RADIUS": self._flow_radius.model.get_value_as_float(),
            "EMISSIVE_INTENSITY": self._flow_emissive,
            "PIPE_OPACITY":    self._pipe_opacity,
            "PIPE_EMISSIVE":   self._pipe_emissive,
        })

    # ── 4. Animated Nodes ─────────────────────────────────────

    def _section_nodes(self):
        with ui.CollapsableFrame("Intersection Nodes", collapsed=False):
            with ui.VStack(spacing=5):
                # Visibility
                with ui.HStack(height=22):
                    cb = ui.CheckBox(width=20)
                    cb.model.set_value(True)
                    ui.Label("Visible")
                    cb.model.add_value_changed_fn(
                        lambda m: _set_visible(PATHS["nodes"],
                                               m.get_value_as_bool()))

                # Live sliders
                self._slider_row("Node Opacity", 0.0, 1.0,
                                 self._node_opacity,
                                 self._on_node_opacity)
                self._slider_row("Node Glow", 0.0, 10.0,
                                 self._node_emissive,
                                 self._on_node_emissive)

                # Regen params
                ui.Separator()
                ui.Label("Regeneration Parameters:", height=20)

                with ui.HStack(height=22):
                    ui.Label("Min Radius:", width=120)
                    self._node_min_r = ui.FloatField(height=20)
                    self._node_min_r.model.set_value(6.0)

                with ui.HStack(height=22):
                    ui.Label("Max Radius:", width=120)
                    self._node_max_r = ui.FloatField(height=20)
                    self._node_max_r.model.set_value(45.0)

                with ui.HStack(height=22):
                    ui.Label("Pulse Amplitude:", width=120)
                    self._node_pulse = ui.FloatField(height=20)
                    self._node_pulse.model.set_value(0.25)

                ui.Button("Regenerate Nodes",
                          clicked_fn=self._regen_nodes,
                          height=28)

    def _on_node_opacity(self, model):
        val = model.get_value_as_float()
        self._node_opacity = val
        _update_shaders(PATHS["nodes"], "opacity_constant", val)

    def _on_node_emissive(self, model):
        val = model.get_value_as_float()
        self._node_emissive = val
        _update_shaders(PATHS["nodes"], "emissive_intensity", val)

    def _regen_nodes(self):
        self._inject_and_run(SCRIPTS["nodes"], {
            "MIN_NODE_RADIUS":  self._node_min_r.model.get_value_as_float(),
            "MAX_NODE_RADIUS":  self._node_max_r.model.get_value_as_float(),
            "PULSE_AMPLITUDE":  self._node_pulse.model.get_value_as_float(),
            "EMISSIVE_INTENSITY": self._node_emissive,
            "NODE_OPACITY":     self._node_opacity,
        })

    # ── 5. Zone Visualization ─────────────────────────────────

    def _section_zones(self):
        with ui.CollapsableFrame("Zone Demand", collapsed=False):
            with ui.VStack(spacing=5):
                # Visibility toggles (origin / dest)
                with ui.HStack(height=22):
                    cb_o = ui.CheckBox(width=20)
                    cb_o.model.set_value(True)
                    ui.Label("Origin Demand", width=120)

                    cb_d = ui.CheckBox(width=20)
                    cb_d.model.set_value(False)
                    ui.Label("Dest Demand")

                cb_o.model.add_value_changed_fn(
                    lambda m: _set_visible(PATHS["zones_origin"],
                                           m.get_value_as_bool()))
                cb_d.model.add_value_changed_fn(
                    lambda m: _set_visible(PATHS["zones_dest"],
                                           m.get_value_as_bool()))

                # Live sliders
                self._slider_row("Zone Opacity", 0.0, 1.0,
                                 self._zone_opacity,
                                 self._on_zone_opacity)
                self._slider_row("Zone Glow", 0.0, 5.0,
                                 self._zone_emissive,
                                 self._on_zone_emissive)

                # Regen params
                ui.Separator()
                ui.Label("Regeneration Parameters:", height=20)

                with ui.HStack(height=22):
                    ui.Label("Column Top (m):", width=120)
                    self._zone_top = ui.FloatField(height=20)
                    self._zone_top.model.set_value(250.0)

                with ui.HStack(height=22):
                    ui.Label("Slab Z (m):", width=120)
                    self._zone_slab = ui.FloatField(height=20)
                    self._zone_slab.model.set_value(1.0)

                with ui.HStack(height=22):
                    ui.Label("Log Scale:", width=120)
                    self._zone_log = ui.CheckBox(width=20)
                    self._zone_log.model.set_value(True)

                ui.Button("Regenerate Zones",
                          clicked_fn=self._regen_zones,
                          height=28)

    def _on_zone_opacity(self, model):
        val = model.get_value_as_float()
        self._zone_opacity = val
        _update_shaders(PATHS["zones"], "opacity_constant", val)

    def _on_zone_emissive(self, model):
        val = model.get_value_as_float()
        self._zone_emissive = val
        _update_shaders(PATHS["zones"], "emissive_intensity", val)

    def _regen_zones(self):
        self._inject_and_run(SCRIPTS["zones"], {
            "ZONE_COLUMN_TOP": self._zone_top.model.get_value_as_float(),
            "SLAB_Z":          self._zone_slab.model.get_value_as_float(),
            "ZONE_OPACITY":    self._zone_opacity,
            "ZONE_EMISSIVE":   self._zone_emissive,
            "USE_LOG_SCALE":   self._zone_log.model.get_value_as_bool(),
        })

    # ── 6. Noise Decay ────────────────────────────────────────

    def _section_noise(self):
        with ui.CollapsableFrame("Noise Decay", collapsed=False):
            with ui.VStack(spacing=5):
                # Visibility
                with ui.HStack(height=22):
                    cb = ui.CheckBox(width=20)
                    cb.model.set_value(True)
                    ui.Label("Visible")
                    cb.model.add_value_changed_fn(
                        lambda m: _set_visible(PATHS["noise"],
                                               m.get_value_as_bool()))

                # Live sliders
                self._slider_row("Shell Opacity", 0.0, 0.5,
                                 self._noise_opacity,
                                 self._on_noise_opacity)
                self._slider_row("Shell Glow", 0.0, 5.0,
                                 self._noise_emissive,
                                 self._on_noise_emissive)

                # Regen params
                ui.Separator()
                ui.Label("Regeneration Parameters:", height=20)

                with ui.HStack(height=22):
                    ui.Label("Ambient dB:", width=120)
                    self._noise_ambient = ui.FloatField(height=20)
                    self._noise_ambient.model.set_value(45.0)

                with ui.HStack(height=22):
                    ui.Label("Pulse Boost:", width=120)
                    self._noise_pulse = ui.FloatField(height=20)
                    self._noise_pulse.model.set_value(0.18)

                with ui.HStack(height=22):
                    ui.Label("Wave Speed (m/s):", width=120)
                    self._noise_wave = ui.FloatField(height=20)
                    self._noise_wave.model.set_value(80.0)

                with ui.HStack(height=22):
                    ui.Label("Max Roads:", width=120)
                    self._noise_max_roads = ui.IntField(height=20)
                    self._noise_max_roads.model.set_value(300)

                ui.Button("Regenerate Noise",
                          clicked_fn=self._regen_noise,
                          height=28)

    def _on_noise_opacity(self, model):
        val = model.get_value_as_float()
        self._noise_opacity = val
        _update_shaders(PATHS["noise"], "opacity_constant", val)

    def _on_noise_emissive(self, model):
        val = model.get_value_as_float()
        self._noise_emissive = val
        _update_shaders(PATHS["noise"], "emissive_intensity", val)

    def _regen_noise(self):
        self._inject_and_run(SCRIPTS["noise"], {
            "BASE_OPACITY":  self._noise_opacity,
            "EMISSIVE_BASE": self._noise_emissive,
            "AMBIENT_DB":    self._noise_ambient.model.get_value_as_float(),
            "PULSE_BOOST":   self._noise_pulse.model.get_value_as_float(),
            "WAVE_SPEED":    self._noise_wave.model.get_value_as_float(),
            "MAX_ROADS":     self._noise_max_roads.model.get_value_as_int(),
        })

    # ── 7. Cesium Tile Coloring ───────────────────────────────

    def _section_tiles(self):
        with ui.CollapsableFrame("Cesium Tile Coloring", collapsed=True):
            with ui.VStack(spacing=5):
                ui.Label("Mode:", height=20)
                with ui.HStack(height=22):
                    self._tile_mode_origin = ui.RadioButton(width=20)
                    ui.Label("Origin", width=80)
                    self._tile_mode_dest = ui.RadioButton(width=20)
                    ui.Label("Destination")
                self._tile_mode_origin.model.set_value(True)

                with ui.HStack(height=22):
                    ui.Label("Tile Opacity:", width=120)
                    self._tile_opacity = ui.FloatField(height=20)
                    self._tile_opacity.model.set_value(0.45)

                with ui.HStack(height=22):
                    ui.Label("Tile Emissive:", width=120)
                    self._tile_emissive_f = ui.FloatField(height=20)
                    self._tile_emissive_f.model.set_value(0.8)

                ui.Button("Color Tiles (snapshot)",
                          clicked_fn=self._regen_tiles,
                          height=28)

                ui.Label("Re-run after moving camera to color new tiles.",
                         height=20,
                         style={"color": 0xFF888888, "font_size": 12})

    def _regen_tiles(self):
        mode = "origin" if self._tile_mode_origin.model.get_value_as_bool() \
               else "destination"
        self._inject_and_run(SCRIPTS["tiles"], {
            "DEMAND_MODE":  mode,
            "TILE_OPACITY": self._tile_opacity.model.get_value_as_float(),
            "TILE_EMISSIVE": self._tile_emissive_f.model.get_value_as_float(),
        })

    # ── Shared helpers ────────────────────────────────────────

    def _slider_row(self, label, lo, hi, default, callback):
        """Create a labelled float slider row."""
        with ui.HStack(height=22):
            ui.Label(f"{label}:", width=120)
            s = ui.FloatSlider(min=lo, max=hi, height=20)
            s.model.set_value(default)
            val_label = ui.Label(f"{default:.2f}", width=45)

        def _on_change(model, _lbl=val_label, _cb=callback):
            _lbl.text = f"{model.get_value_as_float():.2f}"
            _cb(model)

        s.model.add_value_changed_fn(_on_change)

    def _inject_and_run(self, script_path, overrides):
        """Read a generation script, override constants, and execute it.

        For each key in *overrides* the FIRST assignment line matching
        ``KEY = ...`` is replaced with ``KEY = <new_value>``.
        """
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            print(f"ERROR reading {script_path}: {e}")
            return

        patched = []
        used = set()
        for line in lines:
            stripped = line.lstrip()
            replaced = False
            for key, val in overrides.items():
                if key in used:
                    continue
                # Match lines like:  KEY = <anything>   or  KEY  = <anything>
                prefix = key + " "
                prefix2 = key + "="
                if stripped.startswith(prefix) or stripped.startswith(prefix2):
                    # Preserve leading whitespace
                    indent = line[:len(line) - len(stripped)]
                    if isinstance(val, bool):
                        patched.append(f"{indent}{key} = {val}\n")
                    elif isinstance(val, str):
                        patched.append(f'{indent}{key} = "{val}"\n')
                    else:
                        patched.append(f"{indent}{key} = {val}\n")
                    used.add(key)
                    replaced = True
                    break
            if not replaced:
                patched.append(line)

        code = "".join(patched)
        print(f"\n--- Running {script_path} with overrides: {overrides} ---")
        exec(compile(code, script_path, "exec"), {"__name__": "__main__"})

    def destroy(self):
        if self._window:
            self._window.destroy()
            self._window = None


# ── Launch ────────────────────────────────────────────────────

# Close previous instance if re-running in Script Editor
if "traffic_dashboard" in dir():
    try:
        traffic_dashboard.destroy()
    except Exception:
        pass

traffic_dashboard = TrafficDashboard()
print("Traffic Dashboard opened.")
