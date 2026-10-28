# RapidXPy Omniverse Extension

An interactive 3D visualization and digital twin system for traffic modeling, environmental impact assessment, and urban equity, built for NVIDIA Omniverse and Isaac Sim.

## Overview

This extension integrates **RapidXPy** into Omniverse, providing a rich, dockable UI to generate, assign, and visualize city-scale traffic networks in real-time. It bridges traffic dynamics, environmental impacts (like noise and emissions), and urban context into a single spatially coherent 3D environment.

## Key Features

* **Automated City Building:** Download OpenStreetMap (OSM) road networks and building footprints directly into USD using automated Python subprocesses.
* **Geospatial Context:** Seamless integration with Cesium for Omniverse to place networks accurately on WGS-84 Google Photorealistic 3D Tiles.
* **Traffic Assignment:** Run Genetic Algorithm-based PCU estimations and traffic assignments (MSA, FW, CFW) using the RapidXPy core.
* **Transit & Accessibility:** Route generation and isochrone mapping using OpenTripPlanner (OTP), plus multi-modal GTFS analysis.
* **Advanced 3D Visualizations:**
  * **Traffic Flow Particles:** Animated vehicles flowing through edges colored by speed and congestion.
  * **Animated Nodes:** Intersection cylinders that pulse and scale based on traffic volume.
  * **Environmental Noise Modeling:**
    * Concentric 3D tube shells (CNOSSOS-EU) that visually encode noise propagation decay.
    * 3D Cartographic Continuous Hue draping (ribbon width/height encodes dB).
    * GeoTIFF raster heatmap draping over terrain.
  * **Metrics Viewports:** Dedicated color-coded overlays for spatial Equity, Accessibility, and Carbon Emissions.
* **AI Assistant Interface:** Integrated local LLM support via Ollama for conceptual Q&A and natural language command execution (e.g., *"download Munich network"* or *"apply H3 zoning"*).

## Dependencies

The extension automatically attempts to bootstrap its environment by pip-installing required scientific libraries (like `pandas`, `geopandas`, `osmnx`, `networkx`, `shapely`, etc.) on startup.

* **NVIDIA Isaac Sim** (Tested on 4.5+)
* **Cesium for Omniverse** (Auto-enabled by the extension)
* **RapidXPy Environment:** Ensure you have the RapidXPy conda environment configured if you plan to use advanced routing algorithms.

## Usage Guide

1. **Enable the Extension:** Load `my_rapidex.my_python_ui_extension` in your Isaac Sim Extension Manager.
2. **Open the Dashboard:** The "RapidXPy Traffic Digital Twin" window will appear.
3. **Build a Network:**
   * Enter a city name (e.g., `Dresden, Germany`).
   * Set your desired output path.
   * Click **Build Network** to download OSM data and convert it to 3D USD primitives automatically.
4. **Analyze & Visualize:**
   * Use **Traffic Planning** to run assignments and generate `.csv` volume results.
   * Use **Traffic Visualization** to animate the traffic flow or style the intersection nodes.
   * Use **Traffic Analysis** to generate 3D noise decay shells and environmental impact metrics.

## Noise Operations (CNOSSOS-EU)

The extension features a sophisticated environmental noise modeling pipeline based on the **CNOSSOS-EU** (Common Noise Assessment Methods in Europe) standard. This allows planners to evaluate the acoustic impact of varying traffic scenarios directly in 3D:

1. **Noise Computation (`noise_ops.py`):** 
   * Reads traffic volume and speed from assignment results (e.g., `Elite0LinkResults.csv`).
   * Calculates the equivalent continuous sound level ($L_{eq}$) per road link based on the traffic flow and speed parameters.
   * Computes cylindrical distance decay across predefined distance bands (12m, 30m, 60m, 120m, 200m).
   * Automatically saves geospatial datasets (`link_noise.geojson`, `noise_decay_bands.geojson`).
2. **3D Shell Visualization (`noise_viz.py`):**
   * Generates concentric 3D tube shells around road centerlines.
   * Color-codes shells based on WHO health categories (Green for safe < 55 dB, up to Red for harmful > 75 dB).
   * Utilizes an opacity-stratified design so that underlying building footprints remain visible, physically encoding *distance decay* directly into the 3D geometry.
3. **Cartographic & Raster Draping (`noise_viz.py`, `noise_raster.py`):**
   * **Cartographic Continuous Hue Ribbons:** Extrudes 3D ribbons along the road network to visually encode continuous noise data.
     * **Spectral Color Mapping:** Employs the EU-standard continuous spectral hue colormap (quiet blue to loud red) to represent varying $L_{eq}$ levels, accompanied by a dynamic floating UI legend.
     * **Dynamic 3D Scaling:** The physical footprint (width and height) of these ribbons can be scaled dynamically via the UI, directly encoding the magnitude of localized dB levels into the geometry.
   * **Raster Heatmaps:** Uses `rasterio` to generate a 2D GeoTIFF heatmap of the noise boundaries and applies it as a texture overlay draped seamlessly over the Cesium terrain.
4. **Building Exposure & Equity:**
   * Performs spatial joins between the noise decay bands and downloaded OSM building footprints.
   * Calculates the overall noise exposure per building and computes environmental equity metrics (like the Gini coefficient) to help uncover demographic disparities in noise burden.

## Extension Structure

* `extension.py`: Main UI and Kit event loop lifecycle management.
* `geojson_usd_writer.py`: High-performance batch conversion of GeoJSON features to USD meshes.
* `network_ops.py`: RapidXPy wrappers for downloading networks and applying zoning.
* `noise_viz.py` & `noise_ops.py`: CNOSSOS-EU logic and 3D representation generators for traffic noise.
* `traffic_visualization.py`: Particle flow animators and procedural styling for roads and nodes.
* `metrics.py`: Calculation of Gini coefficients, equity, and emission overlays.

## License

SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary

*Note: This is a proprietary extension developed for research and digital twin simulation.*
