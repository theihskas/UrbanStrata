"""
ui_components.py
================
Floating UI windows (legends, network design edit panel, comparison tools)
extracted from extension.py to keep it organized.
"""

import omni.ui as _ui
import re as _re
import my_rapidex.my_python_ui_extension.traffic_visualization as _traffic_viz

def build_traffic_flow_legend(vc_stats, apply_custom_cb):
    if not vc_stats:
        return None

    def fmt(val):
        try:
            return f"{float(val):,.2f}"
        except (ValueError, TypeError):
            return str(val)

    def fmt_int(val):
        try:
            return f"{int(float(val)):,}"
        except (ValueError, TypeError):
            return str(val)

    q1_b   = vc_stats.get("q1_bound", vc_stats.get("q25",  25.0))
    q2_b   = vc_stats.get("q2_bound", vc_stats.get("q50",  50.0))
    q3_b   = vc_stats.get("q3_bound", vc_stats.get("q75",  75.0))
    vc_max = vc_stats.get("max", 100.0)

    quartile_rows = [
        (f"Q1   0 - {q1_b:.1f}%",        "light green", _traffic_viz.QUARTILE_ABGR[0]),
        (f"Q2   {q1_b:.1f} - {q2_b:.1f}%", "yellow",    _traffic_viz.QUARTILE_ABGR[1]),
        (f"Q3   {q2_b:.1f} - {q3_b:.1f}%", "orange",    _traffic_viz.QUARTILE_ABGR[2]),
        (f"Q4   {q3_b:.1f} - {vc_max:.1f}%","dark red",  _traffic_viz.QUARTILE_ABGR[3]),
    ]

    win = _ui.Window(
        "V/C Ratio - Traffic Load", width=340, height=630,
        flags=_ui.WINDOW_FLAGS_NO_COLLAPSE,
    )
    s_lbl = {"font_size": 11, "color": 0xFFCCCCCC}
    s_dim = {"font_size": 10, "color": 0xFF888888}
    s_hdr = {"font_size": 12, "color": 0xFFEEEEEE}
    split_models = []

    with win.frame:
        with _ui.VStack(spacing=4):
            _ui.Label("Traffic Load  -  V/C Ratio (%)", style={"font_size": 13, "color": 0xFFFFFFFF}, height=22)
            _ui.Spacer(height=2)
            
            _ui.Label("Network & Estimation Stats", style=s_hdr, height=18)
            def stat_row(label, val):
                with _ui.HStack(height=18, spacing=8):
                    _ui.Label(label, width=160, style=s_dim)
                    _ui.Label(str(val), style=s_lbl)
            
            stat_row("Total Network Demand:", fmt_int(vc_stats.get("total_demand", "N/A")))
            stat_row("Total System Travel Time:", fmt(vc_stats.get("tstt", "N/A")))
            stat_row("Total Roads:", fmt_int(vc_stats.get("num_roads", "N/A")))
            stat_row("Total Nodes:", fmt_int(vc_stats.get("num_nodes", "N/A")))
            stat_row("Average Time (min):", fmt(vc_stats.get("avg_time", "N/A")))
            stat_row("Congestion (%):", fmt(vc_stats.get("congestion", "N/A")))
            stat_row("Weighted Link Congestion:", fmt(vc_stats.get("weighted_link_cong", "N/A")))
            stat_row("Weighted V/C Ratio:", fmt(vc_stats.get("weighted_vc", "N/A")))
            stat_row("Vehicle KM Travelled:", fmt(vc_stats.get("vkt", "N/A")))
            _ui.Spacer(height=4)

            _ui.Label("Statistics", style=s_hdr, height=18)
            with _ui.HStack(height=18, spacing=16):
                _ui.Label(f"Mean:    {vc_stats.get('mean',   0):.1f}%", style=s_lbl)
                _ui.Label(f"Median:  {vc_stats.get('median', 0):.1f}%", style=s_lbl)
            with _ui.HStack(height=18, spacing=16):
                _ui.Label(f"Mode:    {vc_stats.get('mode',   0):.1f}%", style=s_lbl)
                _ui.Label(f"Max:     {vc_stats.get('max',    0):.1f}%", style=s_lbl)
            _ui.Label(f"Roads with volume data: {vc_stats.get('count', 0)}", style=s_dim, height=16)
            _ui.Spacer(height=4)
            _ui.Label("Quartile Colors  (V/C %)", style=s_hdr, height=18)
            for row_lbl, tier, abgr in quartile_rows:
                with _ui.HStack(height=22, spacing=8):
                    _ui.Rectangle(width=18, style={"background_color": abgr, "border_radius": 3})
                    _ui.Label(f"{row_lbl}   ({tier})", style=s_lbl)
            _ui.Spacer(height=4)
            _ui.Label("Custom Splits (V/C %)", style=s_hdr, height=18)
            _ui.Label("Set Q1|Q2, Q2|Q3 and Q3|Q4 boundary values:", style=s_dim, height=14, word_wrap=True)
            with _ui.HStack(height=26, spacing=6):
                for default_val, lbl in [(q1_b, "Q1|Q2"), (q2_b, "Q2|Q3"), (q3_b, "Q3|Q4")]:
                    _ui.Label(lbl + ":", style=s_dim, width=42)
                    ff = _ui.FloatField(height=24, width=58)
                    ff.model.set_value(float(default_val))
                    split_models.append(ff.model)
            _ui.Spacer(height=4)
            _ui.Button("Apply Custom Ranges", clicked_fn=lambda: apply_custom_cb([m.get_value_as_float() for m in split_models]), 
                       height=30, style={"background_color": 0xFF334455, "color": 0xFFEEEEEE, "border_radius": 4})
    return win

def build_noise_legend(status_msg=""):
    db_range = ""
    m = _re.search(r"dB range ([\d.]+)-([\d.]+)", status_msg)
    if m:
        db_range = f"  ({m.group(1)}-{m.group(2)} dB in dataset)"
    win = _ui.Window("Noise Level Legend", width=230, height=170,
                     flags=(_ui.WINDOW_FLAGS_NO_COLLAPSE | _ui.WINDOW_FLAGS_NO_RESIZE | _ui.WINDOW_FLAGS_NO_SCROLLBAR))
    legend = [("> 85 dB  (very loud)", 0xFF0D1AF2), ("65-85 dB  (loud)", 0xFF0D8CFF),
              ("55-65 dB  (moderate)", 0xFF1AD9B3), ("45-55 dB  (quiet)", 0xFF33A633)]
    with win.frame:
        with _ui.VStack(spacing=6):
            _ui.Label(f"Traffic Noise  (CNOSSOS-EU){db_range}", style={"font_size": 13, "color": 0xFFDDDDDD}, height=20)
            _ui.Spacer(height=2)
            for label_text, abgr in legend:
                with _ui.HStack(height=22, spacing=8):
                    _ui.Rectangle(width=20, style={"background_color": abgr, "border_radius": 3})
                    _ui.Label(label_text, style={"color": 0xFFCCCCCC})
            _ui.Spacer(height=4)
            _ui.Label("Shells: 12 / 30 / 60 / 120 / 200 m from road", style={"font_size": 11, "color": 0xFF999999}, height=16)
    return win

def build_cartographic_legend(status_msg=""):
    db_range = ""
    m = _re.search(r"dB (\d+)-(\d+)", status_msg)
    if m:
        db_range = f"  ({m.group(1)}-{m.group(2)} dB)"
    win = _ui.Window("Cartographic Noise Legend", width=230, height=220,
                     flags=(_ui.WINDOW_FLAGS_NO_COLLAPSE | _ui.WINDOW_FLAGS_NO_RESIZE | _ui.WINDOW_FLAGS_NO_SCROLLBAR))
    legend = [("Loudest (Red)", 0xFF0000FF), ("Loud (Orange)", 0xFF0080FF), ("Moderate (Yellow)", 0xFF00FFFF),
              ("Quiet (Green)", 0xFF00FF00), ("Quieter (Cyan)", 0xFFFFFF00), ("Quietest (Blue)", 0xFFFF0000)]
    with win.frame:
        with _ui.VStack(spacing=6):
            _ui.Label(f"Traffic Noise (Spectral){db_range}", style={"font_size": 13, "color": 0xFFDDDDDD}, height=20)
            _ui.Spacer(height=2)
            for label_text, abgr in legend:
                with _ui.HStack(height=22, spacing=8):
                    _ui.Rectangle(width=20, style={"background_color": abgr, "border_radius": 3})
                    _ui.Label(label_text, style={"color": 0xFFCCCCCC})
            _ui.Spacer(height=4)
            _ui.Label("Ribbon width/height encodes dB", style={"font_size": 11, "color": 0xFF999999}, height=16)
    return win

def build_nd_edit_panel(feature_idx, props, prim_path, pending_edits, apply_cb, remove_cb):
    road_types = ["motorway", "trunk", "primary", "secondary", "tertiary", "residential", "service", "other"]
    btype = str(props.get("highway", props.get("type", "secondary")))
    type_idx = road_types.index(btype) if btype in road_types else 3

    # Parse current capacity — GeoJSON may store it as int, float, or string
    _raw_cap = props.get("capacity", props.get("cap", 1800))
    try:
        _default_cap = float(str(_raw_cap).strip() or 1800)
    except (ValueError, TypeError):
        _default_cap = 1800.0

    win = _ui.Window(f"Edit Road - {prim_path.split('/')[-1]}", width=360, height=440, flags=_ui.WINDOW_FLAGS_NO_COLLAPSE)
    s_lbl, s_dim = {"font_size": 15, "color": 0xFFCCCCCC}, {"font_size": 13, "color": 0xFF888888}
    with win.frame:
        with _ui.VStack(spacing=8):
            _ui.Label(f"Road: {prim_path.split('/')[-1]}", style={"font_size": 16, "color": 0xFFFFFF99}, height=26)
            _ui.Label(f"Edge #{feature_idx}  |  {props.get('from', '?')} -> {props.get('to', '?')}", style=s_dim, height=20)
            _ui.Label(f"Type: {btype}  |  Length: {float(props.get('length', 0) or 0):.0f} m", style=s_dim, height=20)
            _ui.Separator()
            with _ui.HStack(height=30, spacing=6):
                _ui.Label("Lanes:", width=160, style=s_lbl)
                lanes_f = _ui.IntField(height=26)
                lanes_f.model.set_value(int(props.get("lanes", 1) or 1))
            with _ui.HStack(height=30, spacing=6):
                _ui.Label("Max speed (km/h):", width=160, style=s_lbl)
                speed_f = _ui.FloatField(height=26)
                speed_f.model.set_value(float(str(props.get("maxspeed", 50) or 50).replace("mph", "").replace("km/h", "").strip() or 50))
            with _ui.HStack(height=30, spacing=6):
                _ui.Label("Capacity (veh/hr):", width=160, style=s_lbl)
                cap_f = _ui.FloatField(height=26)
                cap_f.model.set_value(_default_cap)
            with _ui.HStack(height=30, spacing=6):
                _ui.Label("Road type:", width=160, style=s_lbl)
                type_c = _ui.ComboBox(type_idx, *road_types)
            _ui.Separator()
            if feature_idx in pending_edits:
                _ui.Label("Pending edit in queue", style={"font_size": 13, "color": 0xFFFFAA44}, height=20)
            with _ui.HStack(spacing=6):
                _ui.Button(
                    "Apply",
                    clicked_fn=lambda: apply_cb(
                        feature_idx,
                        lanes_f.model.get_value_as_int(),
                        speed_f.model.get_value_as_float(),
                        road_types[type_c.model.get_item_value_model().get_value_as_int()],
                        cap_f.model.get_value_as_float(),
                    ),
                    height=34,
                    style={"background_color": 0xFF3D4450, "font_size": 15},
                )
                _ui.Button("Remove", clicked_fn=lambda: remove_cb(feature_idx), height=34, style={"background_color": 0xFF553333, "color": 0xFFEEEEEE, "font_size": 15})
    return win

def build_noise_info_panel(prim_path, noise_db, distance_band, road_idx=None, source_db=None):
    def _rating(db):
        if db >= 75:   return ("Extremely Loud", 0xFF0D1AF2)
        elif db >= 65: return ("Very Loud",      0xFF0D8CFF)
        elif db >= 55: return ("Loud",           0xFF1AD9B3)
        elif db >= 45: return ("Moderate",       0xFF4EA633)
        else:          return ("Quiet",          0xFF3EA63E)

    rating_txt, rating_color = _rating(noise_db)
    prim_name = prim_path.split("/")[-1]
    win = _ui.Window(
        "Noise Band Info", width=270, height=195,
        flags=(_ui.WINDOW_FLAGS_NO_COLLAPSE | _ui.WINDOW_FLAGS_NO_RESIZE | _ui.WINDOW_FLAGS_NO_SCROLLBAR),
    )
    s_lbl = {"font_size": 11, "color": 0xFFCCCCCC}
    s_dim = {"font_size": 10, "color": 0xFF888888}

    with win.frame:
        with _ui.VStack(spacing=5):
            _ui.Label("Selected Noise Band", style={"font_size": 13, "color": 0xFFDDDDDD}, height=20)
            _ui.Separator()
            with _ui.HStack(height=18, spacing=8):
                _ui.Label("Prim:", width=100, style=s_dim)
                _ui.Label(prim_name, style=s_lbl)
            with _ui.HStack(height=18, spacing=8):
                _ui.Label("Distance band:", width=100, style=s_dim)
                _ui.Label(str(distance_band) if distance_band else "N/A", style=s_lbl)
            with _ui.HStack(height=26, spacing=8):
                _ui.Label("Noise level:", width=100, style=s_dim)
                _ui.Label(f"{noise_db:.1f} dB", style={"font_size": 14, "color": 0xFFFFFF44})
            with _ui.HStack(height=22, spacing=8):
                _ui.Label("Rating:", width=100, style=s_dim)
                with _ui.HStack(spacing=6):
                    _ui.Rectangle(width=14, height=14, style={"background_color": rating_color, "border_radius": 3})
                    _ui.Label(rating_txt, style={"font_size": 11, "color": rating_color})
            if source_db is not None:
                with _ui.HStack(height=18, spacing=8):
                    _ui.Label("Road source:", width=100, style=s_dim)
                    _ui.Label(f"{float(source_db):.1f} dB (at centreline)", style=s_lbl)
            if road_idx is not None:
                with _ui.HStack(height=18, spacing=8):
                    _ui.Label("Road index:", width=100, style=s_dim)
                    _ui.Label(str(road_idx), style=s_dim)
    return win


def build_nd_compare_window(orig_stats, mod_stats, n_edits):
    win = _ui.Window("Network Design - Before vs After", width=480, height=420, flags=_ui.WINDOW_FLAGS_NO_COLLAPSE)
    stats_rows = [("Mean V/C %", "mean"), ("Median V/C %", "median"), ("Max V/C %", "max"), ("75th pct V/C", "q75"), ("Road count", "count")]
    with win.frame:
        with _ui.VStack(spacing=6):
            _ui.Label("Before vs After - Traffic Assignment", style={"font_size": 15, "color": 0xFFFFFFFF}, height=26)
            _ui.Label(f"Edits applied: {n_edits} road(s) modified", style={"font_size": 13, "color": 0xFFFFAA44}, height=22)
            _ui.Separator()
            with _ui.HStack(height=24, spacing=4):
                _ui.Label("Metric", width=160, style={"font_size": 13, "color": 0xFFAAAAAA})
                _ui.Label("Before", width=90, style={"font_size": 13, "color": 0xFF44AAFF})
                _ui.Label("After", width=90, style={"font_size": 13, "color": 0xFFFF8844})
                _ui.Label("Change", width=90, style={"font_size": 13, "color": 0xFFCCCCCC})
            _ui.Separator()
            for lbl, key in stats_rows:
                ov, mv = float(orig_stats.get(key, 0) or 0), float(mod_stats.get(key, 0) or 0)
                delta = mv - ov
                c = 0xFF888888 if key == "count" else (0xFF44FF44 if delta < -0.05 else 0xFFFF4444 if delta > 0.05 else 0xFF888888)
                arr = "(+)" if delta > 0.05 else ("(-)" if delta < -0.05 else "  -")
                with _ui.HStack(height=26, spacing=4):
                    _ui.Label(lbl, width=160, style={"font_size": 14, "color": 0xFFCCCCCC})
                    _ui.Label(f"{ov:.1f}" if key != "count" else f"{ov:.0f}", width=90, style={"font_size": 14, "color": 0xFF44AAFF})
                    _ui.Label(f"{mv:.1f}" if key != "count" else f"{mv:.0f}", width=90, style={"font_size": 14, "color": 0xFFFF8844})
                    _ui.Label(f"{arr} {abs(delta):.1f}" if key != "count" else f"{arr} {abs(delta):.0f}", width=90, style={"font_size": 14, "color": c})
    return win