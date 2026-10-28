# UrbanStrata – Interactive Digital Twin for Traffic Modelling and Rapid Planning

UrbanStrata is an NVIDIA Omniverse extension that turns transport-model outputs into an interactive, GPU-rendered 3D digital twin of a city. Road networks, OD-based traffic assignment, congestion, road-traffic noise, equity and accessibility indicators are written as attributes onto georeferenced OpenUSD geometry over Cesium photorealistic 3D tiles, so planners can run network design scenarios and compare before/after results in the real urban context.

**Demo video – network loading in the digital twin:** https://drive.google.com/file/d/1vt8-oGb2SYw0UKR7cRlAx8Eq_en6zC-1/view?usp=sharing

> **Note on RapidXPy.** Demand estimation and traffic assignment are provided by **RapidXPy**, a Python implementation of Rapidex (Waller et al., 2021). RapidXPy is **not part of this repository** and is not open source. Without it, the extension can still load and visualise existing model outputs (GeoJSON); functions that download networks, fetch travel times or run demand estimation and assignment require RapidXPy. Contact the authors (TU Dresden, Chair of Transport Modelling and Simulation) for access.

## Features

### 🏙️ Urban Context & Topology
* **City Network Operations:** Automatically download street networks (via OSM), apply H3 or Square zoning, and build 3D USD road geometry on the fly.
* **Urban Geometry:** Import OSM building footprints with dynamic height/color encoding.
* **Cesium Integration:** Drape Google Photorealistic 3D Tiles and clip terrains to exact city boundaries using the `cesium.omniverse` extension.

### 🚗 Traffic Planning & Simulation
* **Traffic Assignment:** Run PCU estimation (via Genetic Algorithms) and macroscopic traffic assignment algorithms (MSA, FW, CFW, BFW, TAPAS) directly within the Omniverse stage.
* **Advanced Planning:** Decompose OD demands, refine demand with learning rates, and run network robustness analysis.
* **Travel Time APIs:** Fetch live or historic travel times using TomTom, Mapbox, or Google APIs to fix network speeds.
* **Transit Analysis (OTP):** Build OpenTripPlanner graphs, spin up local OTP servers, and run zone-to-zone or point-to-point multi-modal transit analyses. Calculate GTFS zone metrics and mode splits.

### 🎨 3D Traffic Visualization
* **Animated Traffic Flows:** Time-stepped particle-based flows (vehicles) traveling along network edges, colored by speed and congestion.
* **V/C Ratio Quartiles:** Dynamic road pipe coloring and volume labels based on volume-to-capacity thresholds.
* **Intersection Styling:** Animated intersection nodes and centroid markers scaled and colored by traffic volume.
* **Metrics Viewports:** Generate isolated 3D overlay views for Equity (Gini), Accessibility, and Carbon Emissions.

### 🛠️ Interactive Network Design
* **Point-and-Click Editing:** Enable viewport selection to click directly on road meshes in the 3D stage.
* **Scenario Modification:** Edit max speed, lanes, and road types for selected roads.
* **Before/After Comparison:** Rerun traffic assignments on the modified network and launch a floating UI window to compare original vs. modified statistics (V/C %, congestion shifts) in real time.

### 🌍 Environmental Impact (Noise & Emissions)
* **3D Noise Decay Shells (CNOSSOS-EU):** A novel visual representation using concentric tube shells at discrete distance bands (12m, 30m, 60m, 120m, 200m). Encodes noise magnitude and spatial propagation distance intuitively.
* **Cartographic Noise Draping:** Continuous spectral colormaps (blue to red) draped onto annular road ribbons.
* **Raster Heatmaps:** Generate GeoTIFF noise heatmaps based on local ENU projections and apply them as texture overlays on the terrain.
* **Emission Modeling:** Compute per-link CO₂ emissions (exponential speed formula) and aggregate to zone-level polygon meshes.

### 🤖 AI Assistants & Diagnostics
* **AI Command Interface:** Execute operations via natural language (e.g., *"download Munich network"* or *"apply H3 zoning"*) powered by local **Ollama** models (e.g., `llama3.2`).
* **Traffic Modeling Chatbot:** Context-aware Q&A assistant powered by **Anthropic's Claude** (`claude-haiku-4-5-20251001`) to help interpret results and recommend modeling steps.
* **Performance Profiler:** Track Isaac Sim FPS drops, CPU/GPU memory leaks, and USD prim counts over time with exportable CSV reports.

---

## Requirements

* **NVIDIA Isaac Sim 4.5+**
* **Cesium for Omniverse** (`cesium.omniverse` extension enabled)
* **Local Ollama Server** (Optional, required for the AI Command Interface. Must be running `ollama serve` locally)
* **API Keys** (optional, needed only for the features below). **No keys are included in this repository – create your own:**
  * **TomTom** (travel times): register at https://developer.tomtom.com and create an API key.
  * **Google Maps Platform** (alternative travel-time source): create a project and API key at https://console.cloud.google.com.
  * **Mapbox** (alternative travel-time source / terrain tiles): create an access token at https://account.mapbox.com.
  * **Anthropic** (optional Q&A chatbot): create an API key at https://console.anthropic.com.
  * **How to provide them:** paste the key into the *API key* field of the *Travel Time Operations* panel, or set environment variables before launching Omniverse (names listed in `.env.example`), e.g. in PowerShell: `setx TOMTOM_API_KEYS "<your-key>"`. Keep your keys private and never commit them.
* **RapidXPy** (optional, not included – see the note above). Other Python dependencies (pandas, geopandas, osmnx, networkx, etc.) are installed automatically via `omni.kit.pipapi` on startup.

---

## Usage Workflow

1. **Launch:** Enable the extension in the Omniverse Extension Manager. The *RapidXPy Traffic Digital Twin* window will appear.
2. **Base Setup:** Enter a city name (e.g., "Dresden, Germany") and an output base path.
3. **Network Generation:** Click **Build Network** to download OSM data and compile the 3D network.
4. **Traffic Modeling:** Expand the *Traffic Planning* tab. Configure your algorithm (e.g., MSA) and run **Full Plan (PCU + Assign)**.
5. **Visual Analysis:** 
   * Under *Traffic Visualization*, click **Animate Traffic Flow** to spawn vehicles.
   * Under *Traffic Analysis*, compute **Noise (Leq)** and then click **Compute & Visualise 3D Noise Decay**.
6. **Network Intervention:** Open the *Network Design Module*. Enable road selection, click a congested road in the viewport, change its lanes, and click **Run Assignment on Modified Network** to evaluate the impact.

---

## Architecture Notes

* **Asynchronous Execution:** Heavy GIS logic (GeoPandas spatial joins, Dijkstra routing, OSMNX downloads) is offloaded to Python `asyncio` ThreadPoolExecutors to prevent blocking the Isaac Sim main UI thread.
* **Fabric Scene Delegate (FSD):** Used alongside Cesium for high-performance rendering of massive urban environments.
* **Declarative USD Composition:** Generates `Sdf.Layer` sublayers for separate components (Network, Noise, Buildings, Zones), allowing millions of polygons to be toggled instantaneously without memory bloat.

## Codebase Structure

To maintain modularity and keep the Omniverse UI responsive, the codebase is decoupled into UI components, lifecycle mixins, and background operation workers:

* **`extension.py`**: The main entry point. Handles Omniverse lifecycle events (startup/shutdown) and composes the extension using Mixin classes.
* **Mixins (`mixin_*.py`)**: UI callback groupings injected into the main extension class.
  * `mixin_network.py`: Network downloading, zoning, and travel time fetches.
  * `mixin_planning.py`: Traffic assignments, transit analysis (OTP), and multi-modal splits.
  * `mixin_analysis.py`: Environmental analysis (noise, emissions, equity) and visualizations.
  * `mixin_cesium.py`: Cesium georeference and Google Photorealistic 3D Tiles setup.
  * `mixin_nd.py`: Network Design module logic for interactive road edits.
* **UI & Components**:
  * `ui_builder.py`: Constructs the main dockable UI dashboard.
  * `ui_components.py`: Builds floating windows, legends, and comparison dialogs.
* **3D Geometry & Visualization**:
  * `geojson_usd_writer.py`: High-performance, batched conversion of GeoJSON features into USD primitives (Sublayers).
  * `traffic_visualization.py`: Particle-based traffic flow animation, node styling, and V/C quartile coloring.
  * `noise_viz.py` & `noise_raster.py`: 3D noise decay shells, cartographic draping, and GeoTIFF texture overlays.
  * `metrics.py`: Color-coded viewport overlays for equity, accessibility, and carbon.
* **Core Operations (`*_ops.py`)**:
  * `network_ops.py` & `modeling_ops.py`: RapidXPy core wrappers for downloading, zoning, traffic assignments, and OTP servers.
  * `analysis_ops.py` & `noise_ops.py`: Gini index calculation, emissions, and CNOSSOS-EU distance-band decay computation (runs in isolated subprocesses).
  * `building_ops.py`: Downloads OSM building footprints and performs noise spatial joins.
  * `ai_ops.py`: Natural language command parsing and Q&A logic using local Ollama models and Anthropic.
* **Utilities**:
  * `network_design.py`: GeoJSON patching and delta-scenario generation for interactive edits.
  * `fps_diagnostics.py`: CPU/GPU memory and frame-rate profiling.

## Authors & Citation

Sakshi Atul Charde, Durre Nayab, Pallavi Mohan, Steven Travis Waller, Tomasz Bednarz
(TU Dresden – Chair of Transport Modelling and Simulation; NVIDIA Corporation)

If you use this work, please cite:

> Charde, S.A., Nayab, D., Mohan, P., Waller, S.T. and Bednarz, T. (2026) Interactive digital twin for traffic modelling and rapid planning: A GPU-accelerated framework for stakeholder-driven analysis. *European Transport Conference 2026*, Association for European Transport.

Related work:
* Waller, S.T., et al. (2021) Rapidex: A novel tool to estimate origin–destination trips using pervasive traffic data. *Sustainability*, 13(20), 11171.
* Nayab, D., et al. (2026) Exploring urban road traffic noise distribution using automated planning frameworks. *Transportation Research Procedia*, 95, 880–887.

## License

License to be confirmed by the authors. Some files derived from the NVIDIA Omniverse extension template carry NVIDIA licence headers; see those headers for their terms.
